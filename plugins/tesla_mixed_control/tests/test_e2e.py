from .e2e import run


def test_mixed_control_host(tmp_path):
  assert run(tmp_path)["errors"] == []
