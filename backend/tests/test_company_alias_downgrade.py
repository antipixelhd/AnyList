"""Rollback must preserve established cross-provider studio identities."""
import importlib
import unittest
from unittest.mock import MagicMock, patch


class CompanyAliasDowngradeTests(unittest.TestCase):
    def test_existing_alias_refuses_downgrade_before_any_schema_change(self):
        migration=importlib.import_module('migrations.versions.mt049_verified_company_aliases')
        operations=MagicMock()
        operations.get_bind.return_value.scalar.return_value=True
        with patch.object(migration,'op',operations), self.assertRaisesRegex(RuntimeError,'aliases must be preserved'):
            migration.downgrade()
        operations.drop_constraint.assert_not_called()
        operations.drop_index.assert_not_called()
        operations.drop_column.assert_not_called()

    def test_empty_alias_table_can_downgrade(self):
        migration=importlib.import_module('migrations.versions.mt049_verified_company_aliases')
        operations=MagicMock()
        operations.get_bind.return_value.scalar.return_value=False
        with patch.object(migration,'op',operations):
            migration.downgrade()
        operations.drop_column.assert_called_once_with('catalogue_entities','canonical_company_id')
