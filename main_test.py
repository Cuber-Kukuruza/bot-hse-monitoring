from unittest.mock import patch, mock_open, MagicMock
from main import save_data, execute_ssh_command, get_server_load


def test_save_data():
    with patch("builtins.open", mock_open()) as mocked_open:
        servers = {123: {"192.168.1.1": ("user", "pass", "ssh_client")}}
        thresholds = {"192.168.1.1": (80, 80)}
        save_data()

        mocked_open.assert_any_call("servers_data.pkl", "wb")
        mocked_open.assert_any_call("thresholds_data.pkl", "wb")
        assert mocked_open().write.call_count == 2


def test_execute_ssh_command():
    mock_ssh_client = MagicMock()
    mock_ssh_client.exec_command.return_value = (None, MagicMock(read=lambda: b"cpu 80%"), None)

    result = execute_ssh_command(mock_ssh_client, "top -bn1 | grep 'Cpu(s)'")

    mock_ssh_client.exec_command.assert_called_once_with("top -bn1 | grep 'Cpu(s)'")
    assert result == "cpu 80%"


def test_get_server_load():
    mock_ssh_client = MagicMock()
    mock_ssh_client.exec_command.side_effect = [
        (
        None, MagicMock(read=lambda: b"Cpu(s): 10.0 us,  2.0 sy,  0.0 ni, 88.0 id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st"),
        None),
        (None, MagicMock(read=lambda: b"100.0"), None),
    ]

    cpu_usage, ram_usage = get_server_load(mock_ssh_client)

    assert cpu_usage == 10.0
    assert ram_usage == 100.0



