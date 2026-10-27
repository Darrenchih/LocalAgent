"""Source-controlled release identity, bundled as data in the frozen Host."""
import json
from pathlib import Path
import re
import sys
from .management import Binding


def validate_release_identity(value):
    if (not isinstance(value, dict) or set(value) != {'schema', 'bundle_id', 'artifact_version', 'contract_version', 'execution_mode'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['bundle_id'] != 'localagent-management-v1'
            or type(value['contract_version']) is not int or value['contract_version'] != 1
            or value['execution_mode'] != 'disabled'
            or not isinstance(value['artifact_version'], str)
            or not re.fullmatch(r'\d+\.\d+\.\d+', value['artifact_version'])):
        raise ValueError('invalid management-only production release identity')
    Binding(value['bundle_id'], value['artifact_version'])
    return value


def release_identity():
    path = (Path(sys._MEIPASS) / 'protected-runtime-release.json' if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[1] / 'packaging' / 'protected-runtime-release.json')
    return validate_release_identity(json.loads(path.read_text(encoding='utf-8')))


def release_binding():
    value = release_identity()
    return Binding(value['bundle_id'], value['artifact_version'])
