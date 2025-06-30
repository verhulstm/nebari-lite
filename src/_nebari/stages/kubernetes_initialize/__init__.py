import sys
from typing import Any, Dict, List, Optional, Type

from pydantic import model_validator

from _nebari.stages.base import NebariTerraformStage
from _nebari.stages.tf_objects import (
    NebariHelmProvider,
    NebariKubernetesProvider,
    NebariTerraformState,
)
from nebari import schema
from nebari.hookspecs import NebariStage, hookimpl

class InputVars(schema.Base):
    name: str
    environment: str
    cloud_provider: str
    gpu_enabled: bool = False
    gpu_node_group_names: List[str] = []

class OutputSchema(schema.Base):
    pass


class KubernetesInitializeStage(NebariTerraformStage):
    name = "03-kubernetes-initialize"
    priority = 30

    input_schema = InputSchema
    output_schema = OutputSchema

    def tf_objects(self) -> List[Dict]:
        return [
            NebariTerraformState(self.name, self.config),
            NebariKubernetesProvider(self.config),
            NebariHelmProvider(self.config),
        ]

    def input_vars(self, stage_outputs: Dict[str, Dict[str, Any]]):
        input_vars = InputVars(
            name=self.config.project_name,
            environment=self.config.namespace,
            cloud_provider=self.config.provider.value,
        )

        return input_vars.model_dump()

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

        namespaces = {_.metadata.name for _ in result.items}
        if self.config.namespace not in namespaces:
            print(
                f"ERROR: After stage={self.name} namespace={self.config.namespace} not provisioned within kubernetes cluster"
            )
            sys.exit(1)

        print(f"After stage={self.name} kubernetes initialized successfully")


@hookimpl
def nebari_stage() -> List[Type[NebariStage]]:
    return [KubernetesInitializeStage]
