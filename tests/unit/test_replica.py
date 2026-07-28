"""Test Wiki Replica target configuration."""

from unittest import TestCase
from unittest.mock import MagicMock

from aranami.sources.quarry import Replica


class TestReplicaConfiguration(TestCase):
    """Test project and extension replica targets."""

    @staticmethod
    def test_regular_replica_endpoint() -> None:
        """Build the expected endpoint for a project replica."""
        replica = Replica("zhwiki")

        assert replica.hostname == "zhwiki.analytics.db.svc.wikimedia.cloud"
        assert replica.database == "zhwiki_p"
        assert replica.url.drivername == "mysql+pymysql"
        assert replica.url.query["read_default_file"] == ".my.cnf"

    @staticmethod
    def test_replica_from_site() -> None:
        """Read a project database name from a Pywikibot site."""
        site = MagicMock()
        site.dbName.return_value = "zhwiki"

        replica = Replica.from_site(site)

        assert replica.project == "zhwiki"
        site.dbName.assert_called_once_with()

    @staticmethod
    def test_wikidata_terms_endpoint() -> None:
        """Build the expected endpoint for the termstore replica."""
        replica = Replica.wikidata_terms()

        assert (
            replica.hostname
            == "termstore.wikidatawiki.analytics.db.svc.wikimedia.cloud"
        )
        assert replica.database == "wikidatawiki_p"
        assert replica.url.database == "wikidatawiki_p"
