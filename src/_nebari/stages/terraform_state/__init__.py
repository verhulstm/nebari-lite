import contextlib
import enum
import inspect
import os
import pathlib
import re
from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel, field_validator

from _nebari import utils
from _nebari.provider import opentofu
from _nebari.stages.base import NebariTerraformStage
from _nebari.stages.tf_objects import NebariConfig
from _nebari.utils import (
    modified_environ,
)
from nebari import schema
from nebari.hookspecs import NebariStage, hookimpl

@schema.yaml_object(schema.yaml)
class TerraformStateEnum(str, enum.Enum):
    remote = "remote"
    local = "local"
    existing = "existing"

    @classmethod
    def to_yaml(cls, representer, node):
        return representer.represent_str(node.value)


class TerraformState(schema.Base):
    type: TerraformStateEnum = TerraformStateEnum.remote
    backend: Optional[str] = None
    config: Dict[str, str] = {}


class InputSchema(schema.Base):
    terraform_state: TerraformState = TerraformState()


class OutputSchema(schema.Base):
    pass


class TerraformStateStage(NebariTerraformStage):
    name = "01-terraform-state"
    priority = 10

    input_schema = InputSchema
    output_schema = OutputSchema

    @property
    def template_directory(self):
        return (
            pathlib.Path(inspect.getfile(self.__class__)).parent
            / "template"
            / self.config.provider.value
        )

    @property
    def stage_prefix(self):
        return pathlib.Path("stages") / self.name / self.config.provider.value

    def state_imports(self) -> List[Tuple[str, str]]:
        return []

    def tf_objects(self) -> List[Dict]:
        resources = [NebariConfig(self.config)]
        return resources

    def input_vars(self, stage_outputs: Dict[str, Dict[str, Any]]):
        if (
            self.config.provider == schema.ProviderEnum.local
            or self.config.provider == schema.ProviderEnum.existing
        ):
            return {}
        else:
            ValueError(f"Unknown provider: {self.config.provider}")

    @contextlib.contextmanager
    def deploy(
        self, stage_outputs: Dict[str, Dict[str, Any]], disable_prompt: bool = False
    ):
        self.check_immutable_fields()

        # No need to run tofu init here as it's being called when running the
        # terraform show command, inside check_immutable_fields
        with super().deploy(stage_outputs, disable_prompt, tofu_init=False):
            env_mapping = {}
            with modified_environ(**env_mapping):
                yield

    def check_immutable_fields(self):
        nebari_config_state = self.get_nebari_config_state()
        if not nebari_config_state:
            return

        # compute diff of remote/prior and current nebari config
        nebari_config_diff = utils.JsonDiff(
            nebari_config_state, self.config.model_dump()
        )
        # check if any changed fields are immutable
        for keys, old, new in nebari_config_diff.modified():
            bottom_level_schema = self.config
            if len(keys) > 1:
                for key in keys[:-1]:
                    try:
                        bottom_level_schema = getattr(bottom_level_schema, key)
                    except AttributeError as e:
                        if isinstance(bottom_level_schema, dict):
                            # handle case where value is a dict
                            bottom_level_schema = bottom_level_schema[key]
                        else:
                            raise e

            # Return a default (mutable) extra field schema if bottom level is not a Pydantic model (such as a free-form 'overrides' block)
            if isinstance(bottom_level_schema, BaseModel):
                extra_field_schema = schema.ExtraFieldSchema(
                    **type(bottom_level_schema).model_fields[keys[-1]].json_schema_extra
                    or {}
                )
            else:
                extra_field_schema = schema.ExtraFieldSchema()

            if extra_field_schema.immutable:
                key_path = ".".join(keys)
                raise ValueError(
                    f'Attempting to change immutable field "{key_path}" ("{old}"->"{new}") in Nebari config file.  Immutable fields cannot be changed after initial deployment.'
                )

    def get_nebari_config_state(self) -> dict:
        directory = str(self.output_directory / self.stage_prefix)

        tf_state = opentofu.show(directory)
        nebari_config_state = None

        # get nebari config from state
        for resource in (
            tf_state.get("values", {}).get("root_module", {}).get("resources", [])
        ):
            if resource["address"] == "terraform_data.nebari_config":
                nebari_config_state = resource["values"]["input"]
                break
        return nebari_config_state

    @contextlib.contextmanager
    def destroy(
        self, stage_outputs: Dict[str, Dict[str, Any]], status: Dict[str, bool]
    ):
        with super().destroy(stage_outputs, status):
            env_mapping = {}

            with modified_environ(**env_mapping):
                yield


@hookimpl
def nebari_stage() -> List[Type[NebariStage]]:
    return [TerraformStateStage]
