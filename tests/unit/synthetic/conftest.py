import pytest

from synthetic.generator import load_scenarios
from synthetic.schema import Scenarios


@pytest.fixture(scope="session")
def scenarios() -> Scenarios:
    return load_scenarios()[0]
