from _nebari.provider.opentofu import Data, Provider, Resource, TerraformBackend
from _nebari.utils import (
    deep_merge,
)
from nebari import schema


def NebariKubernetesProvider(nebari_config: schema.Main):
    return Provider(
        "kubernetes",
    )


def NebariHelmProvider(nebari_config: schema.Main):
    return Provider("helm")


def NebariTerraformState(directory: str, nebari_config: schema.Main):
    if nebari_config.terraform_state.type == "local":
        return {}
    elif nebari_config.terraform_state.type == "existing":
        return TerraformBackend(
            nebari_config["terraform_state"]["backend"],
            **nebari_config["terraform_state"]["config"],
        )
    elif nebari_config.provider == "existing":
        optional_kwargs = {}
        if "kube_context" in nebari_config.existing:
            optional_kwargs["config_context"] = nebari_config.existing.kube_context
        return TerraformBackend(
            "kubernetes",
            secret_suffix=f"{nebari_config.escaped_project_name}-{nebari_config.namespace}-{directory}",
            load_config_file=True,
            **optional_kwargs,
        )
    elif nebari_config.provider == "local":
        optional_kwargs = {}
        if "kube_context" in nebari_config.local:
            optional_kwargs["config_context"] = nebari_config.local.kube_context
        return TerraformBackend(
            "kubernetes",
            secret_suffix=f"{nebari_config.escaped_project_name}-{nebari_config.namespace}-{directory}",
            load_config_file=True,
            **optional_kwargs,
        )
    else:
        raise NotImplementedError("state not implemented")


def NebariConfig(nebari_config: schema.Main):
    return Resource("terraform_data", "nebari_config", input=nebari_config.model_dump())
