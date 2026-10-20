"""Runtime provenance must bind each ROS node to this install tree."""

import hashlib
from pathlib import Path

import pytest

from runtime_provenance import match_nodes, NODES, write_new


def fixture(tmp_path):
    expected, children = {}, []
    for index, (package, name, python_script) in enumerate(NODES):
        path = tmp_path / 'install' / package / 'lib' / package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        expected[(package, name)] = path
        children.append({
            'pid': index + 1,
            'argv': ['python3', str(path)] if python_script else [str(path)],
            'process_exe': '/usr/bin/python3' if python_script else str(path.resolve()),
            'process_exe_sha256': ('interpreter' if python_script else
                                   hashlib.sha256(name.encode()).hexdigest()),
        })
    return expected, children


def test_exact_running_node_paths_and_hashes_pass(tmp_path):
    expected, children = fixture(tmp_path)

    matched, errors = match_nodes(children, expected)

    assert not errors
    assert len(matched) == 4
    assert all(item['installed_sha256'] for item in matched)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'other_checkout', 'other_binary'])
def test_misleading_node_provenance_fails(tmp_path, change):
    expected, children = fixture(tmp_path)
    if change == 'missing':
        children.pop()
    elif change == 'duplicate':
        children.append(dict(children[-1], pid=99))
    elif change == 'other_checkout':
        children[1]['argv'][0] = '/tmp/other/tracking_controller_node'
    else:
        children[1]['process_exe_sha256'] = 'other'

    _, errors = match_nodes(children, expected)

    assert errors


def test_provenance_report_refuses_overwrite(tmp_path):
    path = Path(tmp_path) / 'runtime.json'
    write_new(path, {'passed': True})
    with pytest.raises(FileExistsError):
        write_new(path, {'passed': False})
