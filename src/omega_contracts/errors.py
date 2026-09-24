"""Contract error shared by isolated Omega safety boundaries."""


class ContractError(ValueError):
    """A fail-closed contract violation."""


class FeatureAbstentionRequired(ContractError):
    """A declared feature absence requires an explicit consumer abstention."""

    def __init__(self, missing_features: tuple[str, ...]) -> None:
        self.missing_features = missing_features
        super().__init__(
            "feature vector requires abstention for missing fields: "
            + ", ".join(missing_features)
        )
