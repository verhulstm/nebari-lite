import contextlib
import enum
import inspect
import os
import pathlib
import re
import sys
import tempfile
import warnings
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Type, Union

from pydantic import AfterValidator, ConfigDict, Field, field_validator, model_validator

from _nebari import constants
from _nebari.provider import opentofu
from _nebari.stages.base import NebariTerraformStage
from _nebari.stages.kubernetes_services import SharedFsEnum
from _nebari.stages.tf_objects import NebariTerraformState
from _nebari.utils import (
    modified_environ,
)
from nebari import schema
from nebari.hookspecs import NebariStage, hookimpl


def get_kubeconfig_filename():
    return str(pathlib.Path(tempfile.gettempdir()) / "NEBARI_KUBECONFIG")


class LocalInputVars(schema.Base):
    kubeconfig_filename: str = get_kubeconfig_filename()
    kube_context: Optional[str] = None


class ExistingInputVars(schema.Base):
    kube_context: str


class NodeGroup(schema.Base):
    instance: str
    min_nodes: Annotated[int, Field(ge=0)] = 0
    max_nodes: Annotated[int, Field(ge=1)] = 1
    taints: Optional[List[schema.Taint]] = None

    @field_validator("taints", mode="before")
    def validate_taint_strings(cls, taints: list[Any]):
        if taints is None:
            return taints
        return_value = []
        for taint in taints:
            if not isinstance(taint, str):
                return_value.append(taint)
            else:
                parsed_taint = schema.Taint.from_string(taint)
                return_value.append(parsed_taint)

        return return_value


DEFAULT_GENERAL_NODE_GROUP_TAINTS = []
DEFAULT_NODE_GROUP_TAINTS = [
    schema.Taint(key="dedicated", value="nebari", effect="NoSchedule")
]


def set_missing_taints_to_default_taints(node_groups: NodeGroup) -> NodeGroup:
    for node_group_name, node_group in node_groups.items():
        if node_group.taints is None:
            if node_group_name == "general":
                node_group.taints = DEFAULT_GENERAL_NODE_GROUP_TAINTS
            else:
                node_group.taints = DEFAULT_NODE_GROUP_TAINTS
    return node_groups

def _calculate_asg_node_group_map(config: schema.Main):
    return {}

def _calculate_node_groups(config: schema.Main):
    return config.local.model_dump()["node_selectors"]

def node_groups_to_dict(node_groups):
    return {ng_name: ng.model_dump() for ng_name, ng in node_groups.items()}


@contextlib.contextmanager
def kubernetes_provider_context(kubernetes_credentials: Dict[str, str]):
    credential_mapping = {
        "config_path": "KUBE_CONFIG_PATH",
        "config_context": "KUBE_CTX",
        "username": "KUBE_USER",
        "password": "KUBE_PASSWORD",
        "client_certificate": "KUBE_CLIENT_CERT_DATA",
        "client_key": "KUBE_CLIENT_KEY_DATA",
        "cluster_ca_certificate": "KUBE_CLUSTER_CA_CERT_DATA",
        "host": "KUBE_HOST",
        "token": "KUBE_TOKEN",
    }

    credentials = {
        credential_mapping[k]: v
        for k, v in kubernetes_credentials.items()
        if v is not None
    }
    with modified_environ(**credentials):
        yield


class KeyValueDict(schema.Base):
    key: str
    value: str

class LocalProvider(schema.Base):
    kube_context: Optional[str] = None
    node_selectors: Dict[str, KeyValueDict] = {
        "general": KeyValueDict(key="kubernetes.io/os", value="linux"),
        "user": KeyValueDict(key="kubernetes.io/os", value="linux"),
        "worker": KeyValueDict(key="kubernetes.io/os", value="linux"),
    }


class ExistingProvider(schema.Base):
    kube_context: Optional[str] = None
    node_selectors: Dict[str, KeyValueDict] = {
        "general": KeyValueDict(key="kubernetes.io/os", value="linux"),
        "user": KeyValueDict(key="kubernetes.io/os", value="linux"),
        "worker": KeyValueDict(key="kubernetes.io/os", value="linux"),
    }


provider_enum_model_map = {
    schema.ProviderEnum.local: LocalProvider,
    schema.ProviderEnum.existing: ExistingProvider,
}

provider_name_abbreviation_map: Dict[str, str] = {
    value: key.value for key, value in schema.provider_enum_name_map.items()
}

class InputSchema(schema.Base):
    local: Optional[LocalProvider] = None
    existing: Optional[ExistingProvider] = None

    @model_validator(mode="before")
    @classmethod
    def check_provider(cls, data: Any) -> Any:
        if "provider" in data:
            provider: str = data["provider"]
            if hasattr(schema.ProviderEnum, provider):
                # TODO: all cloud providers has required fields, but local and existing don't.
                #  And there is no way to initialize a model without user input here.
                #  We preserve the original behavior here, but we should find a better way to do this.
                if provider in ["local", "existing"] and provider not in data:
                    data[provider] = provider_enum_model_map[provider]()
            else:
                # if the provider field is invalid, it won't be set when this validator is called
                # so we need to check for it explicitly here, and set mode to "before"
                # TODO: this is a workaround, check if there is a better way to do this in Pydantic v2
                raise ValueError(
                    f"'{provider}' is not a valid enumeration member; permitted: local, existing"
                )
            set_providers = {
                provider
                for provider in provider_name_abbreviation_map.keys()
                if provider in data and data[provider]
            }
            expected_provider_config = schema.provider_enum_name_map[provider]
            extra_provider_config = set_providers - {expected_provider_config}
            if extra_provider_config:
                warnings.warn(
                    f"Provider is set to {getattr(provider, 'value', provider)},  but configuration defined for other providers: {extra_provider_config}"
                )

        else:
            set_providers = [
                provider
                for provider in provider_name_abbreviation_map.keys()
                if provider in data
            ]
            num_providers = len(set_providers)
            if num_providers > 1:
                raise ValueError(f"Multiple providers set: {set_providers}")
            elif num_providers == 1:
                data["provider"] = provider_name_abbreviation_map[set_providers[0]]
            elif num_providers == 0:
                data["provider"] = schema.ProviderEnum.local.value

        return data


class NodeSelectorKeyValue(schema.Base):
    key: str
    value: str


class KubernetesCredentials(schema.Base):
    host: str
    cluster_ca_certificate: str
    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    client_certificate: Optional[str] = None
    client_key: Optional[str] = None
    config_path: Optional[str] = None
    config_context: Optional[str] = None


class OutputSchema(schema.Base):
    node_selectors: Dict[str, NodeSelectorKeyValue]
    kubernetes_credentials: KubernetesCredentials
    kubeconfig_filename: str
    nfs_endpoint: Optional[str] = None


class KubernetesInfrastructureStage(NebariTerraformStage):
    """Generalized method to provision infrastructure.

    After successful deployment the following properties are set on
    `stage_outputs[directory]`.
      - `kubernetes_credentials` which are sufficient credentials to
        connect with the kubernetes provider
      - `kubeconfig_filename` which is a path to a kubeconfig that can
        be used to connect to a kubernetes cluster
      - at least one node running such that resources in the
        node_group.general can be scheduled

    At a high level this stage is expected to provision a kubernetes
    cluster on a given provider.
    """

    name = "02-infrastructure"
    priority = 20

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
        pass

    def tf_objects(self) -> List[Dict]:
        return []

    def input_vars(self, stage_outputs: Dict[str, Dict[str, Any]]):
        if self.config.provider == schema.ProviderEnum.local:
            return LocalInputVars(
                kube_context=self.config.local.kube_context
            ).model_dump()
        elif self.config.provider == schema.ProviderEnum.existing:
            return ExistingInputVars(
                kube_context=self.config.existing.kube_context
            ).model_dump()
     

    def check(
        self, stage_outputs: Dict[str, Dict[str, Any]], disable_prompt: bool = False
    ):
        from kubernetes import client, config
        from kubernetes.client.rest import ApiException

        config.load_kube_config(
            config_file=stage_outputs["stages/02-infrastructure"][
                "kubeconfig_filename"
            ]["value"]
        )

        try:
            api_instance = client.CoreV1Api()
            result = api_instance.list_namespace()
        except ApiException:
            print(
                f"ERROR: After stage={self.name} unable to connect to kubernetes cluster"
            )
            sys.exit(1)

        if len(result.items) < 1:
            print(
                f"ERROR: After stage={self.name} no nodes provisioned within kubernetes cluster"
            )
            sys.exit(1)

        print(f"After stage={self.name} kubernetes cluster successfully provisioned")

    def set_outputs(
        self, stage_outputs: Dict[str, Dict[str, Any]], outputs: Dict[str, Any]
    ):
        outputs["node_selectors"] = _calculate_node_groups(self.config)
        super().set_outputs(stage_outputs, outputs)

    @contextlib.contextmanager
    def post_deploy(
        self, stage_outputs: Dict[str, Dict[str, Any]], disable_prompt: bool = False
    ):
        asg_node_group_map = _calculate_asg_node_group_map(self.config)
        if asg_node_group_map:
            amazon_web_services.set_asg_tags(
                asg_node_group_map, self.config.amazon_web_services.region
            )

    @contextlib.contextmanager
    def deploy(
        self, stage_outputs: Dict[str, Dict[str, Any]], disable_prompt: bool = False
    ):
        with super().deploy(stage_outputs, disable_prompt):
            with kubernetes_provider_context(
                stage_outputs["stages/" + self.name]["kubernetes_credentials"]["value"]
            ):
                yield

    @contextlib.contextmanager
    def destroy(
        self, stage_outputs: Dict[str, Dict[str, Any]], status: Dict[str, bool]
    ):
        with super().destroy(stage_outputs, status):
            with kubernetes_provider_context(
                stage_outputs["stages/" + self.name]["kubernetes_credentials"]["value"]
            ):
                yield


@hookimpl
def nebari_stage() -> List[Type[NebariStage]]:
    return [KubernetesInfrastructureStage]
