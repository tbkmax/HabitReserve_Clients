from unittest.mock import patch

from time_bank_tracker import auth


def test_saved_refresh_token_migrates_legacy_keyring_entry() -> None:
    def stored_token(service_name: str, account: str) -> str | None:
        assert account == auth.REFRESH_ACCOUNT
        return "legacy-refresh-token" if service_name == auth.LEGACY_SERVICE_NAME else None

    with (
        patch.object(auth.keyring, "get_password", side_effect=stored_token),
        patch.object(auth.keyring, "set_password") as set_password,
        patch.object(auth.keyring, "delete_password") as delete_password,
    ):
        assert auth.saved_refresh_token() == "legacy-refresh-token"

    set_password.assert_called_once_with(auth.SERVICE_NAME, auth.REFRESH_ACCOUNT, "legacy-refresh-token")
    delete_password.assert_called_once_with(auth.LEGACY_SERVICE_NAME, auth.REFRESH_ACCOUNT)
