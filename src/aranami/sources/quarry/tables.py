"""Map MediaWiki 1.45.1 and selected extension replica tables.

The mappings describe existing Wiki Replica tables for read-only
SQLAlchemy ``select()`` expressions. They are metadata only:
Aranami never creates or mutates these tables.

MediaWiki core follows the 1.45.1 schema diagram. Extension
mappings follow the REL1_45 PageAssessments and Wikibase
Repository abstract schemas.

References:
    - [[mw:Template:SchemaDiagram/sql/mediawiki-1.45.1.json]]
    - [[mw:Extension:PageAssessments#Database tables]]
    - [[mw:Extension:Wikibase Repository#Database]]
"""

from __future__ import annotations

__all__ = (
    "Actor",
    "Archive",
    "Base",
    "BinaryDecoder",
    "Block",
    "BlockTarget",
    "BotPasswords",
    "Category",
    "Categorylinks",
    "ChangeTag",
    "ChangeTagDef",
    "Collation",
    "Comment",
    "Content",
    "ContentModels",
    "EnumDecoder",
    "Existencelinks",
    "Externallinks",
    "File",
    "Filearchive",
    "Filerevision",
    "Filetypes",
    "Image",
    "Imagelinks",
    "Interwiki",
    "IpChanges",
    "IpblocksRestrictions",
    "Iwlinks",
    "Job",
    "L10nCache",
    "Langlinks",
    "Linktarget",
    "LogSearch",
    "Logging",
    "Objectcache",
    "Oldimage",
    "Page",
    "PageAssessments",
    "PageAssessmentsProjects",
    "PageProps",
    "PageRestrictions",
    "Pagelinks",
    "ProtectedTitles",
    "Querycache",
    "QuerycacheInfo",
    "Querycachetwo",
    "Recentchanges",
    "Redirect",
    "Revision",
    "Searchindex",
    "SiteIdentifiers",
    "SiteStats",
    "Sites",
    "SlotRoles",
    "Slots",
    "StringDecoder",
    "Templatelinks",
    "Text",
    "TextDecoder",
    "Updatelog",
    "Uploadstash",
    "User",
    "UserAutocreateSerial",
    "UserFormerGroups",
    "UserGroups",
    "UserNewtalk",
    "UserProperties",
    "Watchlist",
    "WatchlistExpiry",
    "WatchlistLabel",
    "WatchlistLabelMember",
    "WbChanges",
    "WbChangesSubscription",
    "WbIdCounters",
    "WbItemsPerSite",
    "WbPropertyInfo",
    "WbtItemTerms",
    "WbtPropertyTerms",
    "WbtTermInLang",
    "WbtText",
    "WbtTextInLang",
)

from datetime import (
    datetime,  # ruff: ignore[typing-only-standard-library-import]
)
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, override

from sqlalchemy import (
    DateTime,
    Index,
    LargeBinary,
    String,
    Text as SQLText,
)
from sqlalchemy.dialects.mysql import (
    BIGINT,
    FLOAT,
    INTEGER,
    SMALLINT,
    TINYINT,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

if TYPE_CHECKING:
    from sqlalchemy.engine import Dialect


class Base(DeclarativeBase):
    """Provide metadata for read-only Wiki Replica mappings."""


class StringDecoder(TypeDecorator[str]):
    """Normalize values from character columns as strings.

    Wiki Replicas can return bytes for character columns.
    This decorator preserves string query parameters and
    decodes byte results for callers.
    """

    impl = String
    cache_ok = True

    _encoding_config: ClassVar[MappingProxyType[str, str]] = MappingProxyType(
        {
            "encoding": "utf-8",
            "errors": "backslashreplace",
        },
    )

    @override
    def process_bind_param(
        self,
        value: str | None,
        dialect: Dialect,
    ) -> str | None:
        """Preserve a string before executing a query.

        Args:
            value: String value to preserve.
            dialect: Database dialect handling the value.

        Returns:
            The original string, or ``None`` for a null value.
        """
        return value

    def process_result_value(
        self,
        value: str | bytes | None,
        dialect: Dialect,  # ruff: ignore[unused-method-argument]
    ) -> str | None:
        """Normalize a character-column result as text.

        Args:
            value: String or byte value returned by the driver.
            dialect: Database dialect handling the value.

        Returns:
            Decoded text, or ``None`` for a null value.
        """
        if value is None or isinstance(value, str):
            return value
        return value.decode(**self._encoding_config)


class TextDecoder(StringDecoder):
    """Normalize values from text columns as strings."""

    impl = SQLText
    cache_ok = True


class EnumDecoder(StringDecoder):
    """Normalize values from enum columns as strings."""

    cache_ok = True

    def __init__(self, *values: str) -> None:
        """Retain the allowed enum values as query metadata.

        Args:
            *values: Values declared by the abstract schema.
        """
        self.values = values
        super().__init__(
            length=max(map(len, values)) if values else None,
        )


class BinaryDecoder(TypeDecorator[str]):
    """Encode and decode text fields stored as binary values.

    MediaWiki uses binary columns for case-sensitive identifiers.
    This decorator lets callers compare and receive ordinary
    strings while the database driver works with bytes.
    """

    impl = LargeBinary
    cache_ok = True

    _encoding_config: ClassVar[MappingProxyType[str, str]] = MappingProxyType(
        {
            "encoding": "utf-8",
            "errors": "backslashreplace",
        },
    )

    def process_bind_param(
        self,
        value: str | None,
        dialect: Dialect,  # ruff: ignore[unused-method-argument]
    ) -> bytes | None:
        """Encode a string before executing a query.

        Args:
            value: String value to encode.
            dialect: Database dialect handling the value.

        Returns:
            Encoded bytes, or ``None`` for a null value.
        """
        if value is None:
            return None
        return value.encode(**self._encoding_config)

    def process_result_value(
        self,
        value: str | bytes | None,
        dialect: Dialect,  # ruff: ignore[unused-method-argument]
    ) -> str | None:
        """Normalize a binary-column result as text.

        Args:
            value: String or byte value returned by the driver.
            dialect: Database dialect handling the value.

        Returns:
            Decoded text, or ``None`` for a null value.
        """
        if value is None or isinstance(value, str):
            return value
        return value.decode(**self._encoding_config)


class Actor(Base):
    """Map the ``actor`` table.

    The "actor" table associates user names or IP addresses with
    integers for the benefit of other tables that need to refer to
    either logged-in or logged-out users. If something can only ever be
    done by logged-in users, it can refer to the user table directly.
    """

    __tablename__ = "actor"

    __table_args__ = (
        Index(
            "actor_user",
            "actor_user",
            unique=True,
        ),
        Index(
            "actor_name",
            "actor_name",
            unique=True,
        ),
    )

    actor_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Unique ID to identify each actor",
    )

    actor_user: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment="Key to user.user_id, or NULL for anonymous edits",
    )

    actor_name: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Text username or IP address",
    )


class Archive(Base):
    """Map the ``archive`` table.

    Archive area for deleted pages and their revisions. These may be
    viewed (and restored) by admins through the Special:Undelete
    interface.
    """

    __tablename__ = "archive"

    __table_args__ = (
        Index(
            "ar_name_title_timestamp",
            "ar_namespace",
            "ar_title",
            "ar_timestamp",
        ),
        Index(
            "ar_actor_timestamp",
            "ar_actor",
            "ar_timestamp",
        ),
        Index(
            "ar_revid_uniq",
            "ar_rev_id",
            unique=True,
        ),
    )

    ar_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Primary key",
    )

    ar_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment="Copied from page_namespace",
    )

    ar_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Copied from page_title",
    )

    ar_comment_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="Basic revision stuff.",
    )

    ar_actor: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="Basic revision stuff.",
    )

    ar_timestamp: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment="Basic revision stuff.",
    )

    ar_minor_edit: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment="Basic revision stuff.",
    )

    ar_rev_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="Copied from rev_id.",
    )

    ar_deleted: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        comment=(
            "Copied from rev_deleted. Although this may be raised "
            'during deletion. Users with the "suppressrevision" right '
            'may "archive" and "suppress" content in a single action. '
            "@since 1.10"
        ),
    )

    ar_len: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment=(
            "Copied from rev_len, length of this revision in bytes. "
            "@since 1.10"
        ),
    )

    ar_page_id: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment=(
            "Copied from page_id. Restoration will attempt to use "
            "this as page ID if no current page with the same name "
            "exists. Otherwise, the revisions will be restored under "
            "the current page. Can be used for manual undeletion by "
            "developers if multiple pages by the same name were "
            "archived. @since 1.11 Older entries will have NULL."
        ),
    )

    ar_parent_id: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment="Copied from rev_parent_id. @since 1.13",
    )


class Block(Base):
    """Map the ``block`` table.

    Blocks against user accounts, IP addresses and IP ranges.
    """

    __tablename__ = "block"

    __table_args__ = (
        Index(
            "bl_timestamp",
            "bl_timestamp",
        ),
        Index(
            "bl_target",
            "bl_target",
        ),
        Index(
            "bl_expiry",
            "bl_expiry",
        ),
        Index(
            "bl_parent_block_id",
            "bl_parent_block_id",
        ),
    )

    bl_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Primary key.",
    )

    bl_target: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="The block target. Foreign key to block_target.bt_id.",
    )

    bl_by_actor: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="Actor who made the block.",
    )

    bl_reason_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="Key to comment_id. Text comment made by blocker.",
    )

    bl_timestamp: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment=(
            "Creation (or refresh) date in standard YMDHMS form. IP "
            "blocks expire automatically."
        ),
    )

    bl_anon_only: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment=(
            "If set to 1, block applies only to logged-out users and "
            "temporary users."
        ),
    )

    bl_create_account: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment="Block prevents account creation from matching IP addresses",
    )

    bl_enable_autoblock: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment="Block triggers autoblocks",
    )

    bl_expiry: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment='Time at which the block will expire. May be "infinity"',
    )

    bl_deleted: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment=(
            "One of 0, 1, or 2. If 0, the the block is not hidden. If "
            "1, this block causes the username and block to be "
            "hidden, and the expiry must be infinite. Additionally, "
            "for a value of 1 this is denormalized into rev_deleted "
            "and the other deleted bitfields (T346716). For a value "
            "of 2, this just hides the block and leaves the username "
            "unhidden."
        ),
    )

    bl_block_email: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment="Block prevents user from accessing Special:Emailuser",
    )

    bl_allow_usertalk: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment="Block allows user to edit their own talk page",
    )

    bl_parent_block_id: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment=(
            "ID of the block that caused this block to exist. "
            "Autoblocks set this to the original block so that the "
            "original block being deleted also deletes the "
            "autoblocks."
        ),
    )

    bl_sitewide: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment=(
            "Block user from editing any page on the site (other than "
            "their own user talk page)."
        ),
    )


class BlockTarget(Base):
    """Map the ``block_target`` table.

    The targets of blocks
    """

    __tablename__ = "block_target"

    __table_args__ = (
        Index(
            "bt_address",
            "bt_address",
        ),
        Index(
            "bt_ip_user_text",
            "bt_ip_hex",
            "bt_user_text",
        ),
        Index(
            "bt_range",
            "bt_range_start",
            "bt_range_end",
        ),
        Index(
            "bt_user",
            "bt_user",
        ),
    )

    bt_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Primary key.",
    )

    bt_address: Mapped[str | None] = mapped_column(
        BinaryDecoder(255),
        nullable=True,
        comment=(
            "Blocked IP address or range in dotted-quad form, or null "
            "for a user block. If bt_auto is 1 then the address is "
            "private."
        ),
    )

    bt_user: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment="Blocked user ID or null for IP blocks.",
    )

    bt_user_text: Mapped[str | None] = mapped_column(
        BinaryDecoder(255),
        nullable=True,
        comment=(
            "The name of the blocked user, or null for IP blocks. For "
            "Special:BlockList sorting (T48013)."
        ),
    )

    bt_auto: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment=(
            "Indicates that the IP address was banned because a "
            "banned user accessed a page through it. If this is 1, "
            "ipb_address will be hidden, and the block identified by "
            "block ID number."
        ),
    )

    bt_range_start: Mapped[str | None] = mapped_column(
        BinaryDecoder(255),
        nullable=True,
        comment=(
            "Start of an address range, in hexadecimal. Null for "
            "single-IP and user blocks."
        ),
    )

    bt_range_end: Mapped[str | None] = mapped_column(
        BinaryDecoder(255),
        nullable=True,
        comment=(
            "End of an address range, in hexadecimal. Null for "
            "single-IP and user blocks."
        ),
    )

    bt_ip_hex: Mapped[str | None] = mapped_column(
        BinaryDecoder(255),
        nullable=True,
        comment=(
            "If the block is for a single IP, this is the IP address "
            "in hexadecimal. If the block is for a range, the start "
            "of the range in hexadecimal, identical to "
            "bt_range_start."
        ),
    )

    bt_count: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment="The number of block rows associated with this target.",
    )


class BotPasswords(Base):
    """Map the ``bot_passwords`` table.

    This table contains a user's bot passwords: passwords that allow
    access to the account via the API with limited rights.
    """

    __tablename__ = "bot_passwords"

    bp_user: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="User ID obtained from CentralIdLookup.",
    )

    bp_app_id: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        primary_key=True,
        nullable=False,
        comment="Application identifier.",
    )

    bp_password: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Password hashes, like user.user_password.",
    )

    bp_token: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment="Like user.user_token",
    )

    bp_restrictions: Mapped[str] = mapped_column(
        BinaryDecoder(65535),
        nullable=False,
        comment="JSON blob for MWRestrictions",
    )

    bp_grants: Mapped[str] = mapped_column(
        BinaryDecoder(65535),
        nullable=False,
        comment=(
            "Grants allowed to the account when authenticated with "
            "this bot-password"
        ),
    )


class Category(Base):
    """Map the ``category`` table.

    Track all existing categories. Something is a category if 1) it has
    an entry somewhere in categorylinks, or 2) it has a description
    page. Categories might not have corresponding pages, so they need to
    be tracked separately. The numbers of member pages (including
    categories and media), subcategories, and Image: namespace members,
    respectively are included in this table too.  These are signed to
    make underflow more obvious.  We make the first number include the
    second two for better sorting: subtracting for display is easy,
    adding for ordering is not.
    """

    __tablename__ = "category"

    __table_args__ = (
        Index(
            "cat_title",
            "cat_title",
            unique=True,
        ),
        Index(
            "cat_pages",
            "cat_pages",
        ),
    )

    cat_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Primary key",
    )

    cat_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "Name of the category, in the same form as page_title "
            "(with underscores). If there is a category page "
            "corresponding to this category, by definition, it has "
            "this name (in the Category namespace)."
        ),
    )

    cat_pages: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
    )

    cat_subcats: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
    )

    cat_files: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
    )


class Categorylinks(Base):
    """Map the ``categorylinks`` table.

    Track category inclusions *used inline* This tracks a single level
    of category membership
    """

    __tablename__ = "categorylinks"

    __table_args__ = (
        Index(
            "cl_sortkey_id",
            "cl_target_id",
            "cl_type",
            "cl_sortkey",
            "cl_from",
        ),
        Index(
            "cl_timestamp_id",
            "cl_target_id",
            "cl_timestamp",
        ),
    )

    cl_from: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Key to page_id of the page defined as a category member.",
    )

    cl_target_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Foreign key to linktarget.lt_id",
    )

    cl_sortkey: Mapped[str] = mapped_column(
        BinaryDecoder(230),
        nullable=False,
        comment=(
            "A binary string obtained by applying a sortkey "
            "generation algorithm (Collation::getSortKey()) to "
            'page_title, or cl_sortkey_prefix . "\\n" page_title if '
            "cl_sortkey_prefix is nonempty."
        ),
    )

    cl_sortkey_prefix: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "A prefix for the raw sortkey manually specified by the "
            "user, either via [[Category:Foo|prefix]] or "
            "{{defaultsort:prefix}}. If nonempty, it's concatenated "
            "with a line break followed by the page title before the "
            "sortkey conversion algorithm is run. We store this so "
            "that we can update collations without reparsing all "
            "pages. Note: If you change the length of this field, you "
            "also need to change code in LinksUpdate.php. See T27254."
        ),
    )

    cl_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment=(
            "This isn't really used at present. Provided for an "
            "optional sorting method by approximate addition time."
        ),
    )

    cl_type: Mapped[str] = mapped_column(
        EnumDecoder("page", "subcat", "file"),
        nullable=False,
        comment=(
            "Stores whether cl_from is a category, file, or other "
            "page, so we can paginate the three categories "
            "separately. This only has to be updated when moving "
            "pages into or out of the category namespace, since file "
            "pages cannot be moved to other namespaces, nor can non- "
            "files be moved into the file namespace."
        ),
    )

    cl_collation_id: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True),
        nullable=False,
        comment="FK to collation_id",
    )


class ChangeTag(Base):
    """Map the ``change_tag`` table.

    A table to track tags for revisions, logs and recent changes
    """

    __tablename__ = "change_tag"

    __table_args__ = (
        Index(
            "ct_rc_tag_id",
            "ct_rc_id",
            "ct_tag_id",
            unique=True,
        ),
        Index(
            "ct_log_tag_id",
            "ct_log_id",
            "ct_tag_id",
            unique=True,
        ),
        Index(
            "ct_rev_tag_id",
            "ct_rev_id",
            "ct_tag_id",
            unique=True,
        ),
        Index(
            "ct_tag_id_id",
            "ct_tag_id",
            "ct_rc_id",
            "ct_rev_id",
            "ct_log_id",
        ),
    )

    ct_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    ct_rc_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        comment="RCID for the change",
    )

    ct_log_id: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment="LOGID for the change",
    )

    ct_rev_id: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment="REVID for the change",
    )

    ct_params: Mapped[str | None] = mapped_column(
        BinaryDecoder(65530),
        nullable=True,
        comment="Parameters for the tag; used by some extensions",
    )

    ct_tag_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="Foreign key to change_tag_def row",
    )


class ChangeTagDef(Base):
    """Map the ``change_tag_def`` table.

    Table defining tag names for IDs. Also stores hit counts to avoid
    expensive queries on change_tag
    """

    __tablename__ = "change_tag_def"

    __table_args__ = (
        Index(
            "ctd_name",
            "ctd_name",
            unique=True,
        ),
        Index(
            "ctd_count",
            "ctd_count",
        ),
        Index(
            "ctd_user_defined",
            "ctd_user_defined",
        ),
    )

    ctd_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Numerical ID of the tag (ct_tag_id refers to this)",
    )

    ctd_name: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "Symbolic name of the tag (what would previously be put in ct_tag)"
        ),
    )

    ctd_user_defined: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment=(
            "Whether this tag was defined manually by a privileged "
            "user using Special:Tags"
        ),
    )

    ctd_count: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="Number of times this tag was used",
    )


class Collation(Base):
    """Map the ``collation`` table.

    Normalization table for collation names
    """

    __tablename__ = "collation"

    __table_args__ = (
        Index(
            "collation_name",
            "collation_name",
            unique=True,
        ),
    )

    collation_id: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    collation_name: Mapped[str] = mapped_column(
        BinaryDecoder(64),
        nullable=False,
    )


class Comment(Base):
    """Map the ``comment`` table.

    Edits, blocks, and other actions typically have a textual comment
    describing the action. They are stored here to reduce the size of
    the main tables, and to allow for deduplication. Deduplication is
    currently best-effort to avoid locking on inserts that would be
    required for strict deduplication. There MAY be multiple rows with
    the same comment_text and comment_data.
    """

    __tablename__ = "comment"

    __table_args__ = (
        Index(
            "comment_hash",
            "comment_hash",
        ),
    )

    comment_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Unique ID to identify each comment",
    )

    comment_hash: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment="Hash of comment_text and comment_data, for deduplication",
    )

    comment_text: Mapped[str] = mapped_column(
        BinaryDecoder(65535),
        nullable=False,
        comment=(
            "Text comment summarizing the change. This text is shown "
            "in the history and other changes lists, rendered in a "
            "subset of wiki markup by "
            "MediaWiki\\CommentFormatter\\CommentFormatter::format(). "
            "Size limits are enforced at the application level, and "
            "should take care to crop UTF-8 strings appropriately."
        ),
    )

    comment_data: Mapped[str | None] = mapped_column(
        BinaryDecoder(65535),
        nullable=True,
        comment=(
            "JSON data, intended for localizing auto-generated "
            "comments. This holds structured data that is intended to "
            "be used to provide localized versions of automatically- "
            "generated comments. When not empty, comment_text should "
            "be the generated comment localized using the wiki's "
            "content language."
        ),
    )


class Content(Base):
    """Map the ``content`` table.

    The content table represents content objects. It's primary purpose
    is to provide the necessary meta-data for loading and interpreting a
    serialized data blob to create a content object.
    """

    __tablename__ = "content"

    content_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="ID of the content object",
    )

    content_size: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment=(
            "Nominal size of the content object (not necessarily of "
            "the serialized blob)"
        ),
    )

    content_sha1: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment=(
            "Nominal hash of the content object (not necessarily of "
            "the serialized blob)"
        ),
    )

    content_model: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True),
        nullable=False,
        comment=(
            "reference to model_id. Note the content format isn't "
            "specified; it should be assumed to be in the default "
            "format for the model unless auto-detected otherwise."
        ),
    )

    content_address: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="URL-like address of the content blob",
    )


class ContentModels(Base):
    """Map the ``content_models`` table.

    Normalization table for content model names
    """

    __tablename__ = "content_models"

    __table_args__ = (
        Index(
            "model_name",
            "model_name",
            unique=True,
        ),
    )

    model_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    model_name: Mapped[str] = mapped_column(
        BinaryDecoder(64),
        nullable=False,
    )


class Existencelinks(Base):
    """Map the ``existencelinks`` table.

    Track dependencies on the existence of a target page, like #ifexist
    """

    __tablename__ = "existencelinks"

    __table_args__ = (
        Index(
            "exl_target_id",
            "exl_target_id",
            "exl_from",
        ),
    )

    exl_from: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="page_id of the referring page",
    )

    exl_target_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Foreign key to linktarget.lt_id",
    )


class Externallinks(Base):
    """Map the ``externallinks`` table.

    Track links to external URLs
    """

    __tablename__ = "externallinks"

    __table_args__ = (
        Index(
            "el_from",
            "el_from",
        ),
        Index(
            "el_to_domain_index_to_path",
            "el_to_domain_index",
            "el_to_path",
        ),
    )

    el_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    el_from: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="page_id of the referring page",
    )

    el_to_domain_index: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Indexable domain",
    )

    el_to_path: Mapped[str | None] = mapped_column(
        BinaryDecoder(65530),
        nullable=True,
        comment="Path to the external link without considering the domain",
    )


class File(Base):
    """Map the ``file`` table.

    Uploaded images and other files.
    """

    __tablename__ = "file"

    __table_args__ = (
        Index(
            "file_name",
            "file_name",
            unique=True,
        ),
        Index(
            "file_latest",
            "file_latest",
        ),
    )

    file_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="primary key",
    )

    file_name: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Name of the file",
    )

    file_latest: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="FK to fr_id",
    )

    file_type: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True),
        nullable=False,
        comment="FK to filetypes.ft_id",
    )

    file_deleted: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True),
        nullable=False,
        comment="Whether the file is deleted",
    )


class Filearchive(Base):
    """Map the ``filearchive`` table.

    Record of deleted file data
    """

    __tablename__ = "filearchive"

    __table_args__ = (
        Index(
            "fa_name",
            "fa_name",
            "fa_timestamp",
        ),
        Index(
            "fa_storage_group",
            "fa_storage_group",
            "fa_storage_key",
        ),
        Index(
            "fa_deleted_timestamp",
            "fa_deleted_timestamp",
        ),
        Index(
            "fa_actor_timestamp",
            "fa_actor",
            "fa_timestamp",
        ),
        Index(
            "fa_sha1",
            "fa_sha1",
        ),
    )

    fa_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Unique row id",
    )

    fa_name: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "Original base filename; key to image.img_name, "
            "page.page_title, etc"
        ),
    )

    fa_archive_name: Mapped[str | None] = mapped_column(
        BinaryDecoder(255),
        nullable=True,
        comment="Filename of archived file, if an old revision",
    )

    fa_storage_group: Mapped[str | None] = mapped_column(
        BinaryDecoder(16),
        nullable=True,
        comment=(
            "Which storage bin (directory tree or object store) the "
            "file data is stored in. Should be 'deleted' for files "
            "that have been deleted; any other bin is not yet in use."
        ),
    )

    fa_storage_key: Mapped[str | None] = mapped_column(
        BinaryDecoder(64),
        nullable=True,
        comment=(
            "SHA-1 of the file contents plus extension, used as a key "
            "for storage. eg "
            "8f8a562add37052a1848ff7771a2c515db94baa9.jpg. If NULL, "
            "the file was missing at deletion time or has been purged "
            "from the archival storage."
        ),
    )

    fa_deleted_user: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=False),
        nullable=True,
    )

    fa_deleted_timestamp: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
    )

    fa_deleted_reason_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
    )

    fa_size: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
    )

    fa_width: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=False),
        nullable=True,
    )

    fa_height: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=False),
        nullable=True,
    )

    fa_metadata: Mapped[str | None] = mapped_column(
        BinaryDecoder(16777215),
        nullable=True,
    )

    fa_bits: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=False),
        nullable=True,
    )

    fa_media_type: Mapped[str | None] = mapped_column(
        EnumDecoder(
            "UNKNOWN",
            "BITMAP",
            "DRAWING",
            "AUDIO",
            "VIDEO",
            "MULTIMEDIA",
            "OFFICE",
            "TEXT",
            "EXECUTABLE",
            "ARCHIVE",
            "3D",
        ),
        nullable=True,
    )

    fa_major_mime: Mapped[str | None] = mapped_column(
        EnumDecoder(
            "unknown",
            "application",
            "audio",
            "image",
            "text",
            "video",
            "message",
            "model",
            "multipart",
            "chemical",
        ),
        nullable=True,
    )

    fa_minor_mime: Mapped[str | None] = mapped_column(
        BinaryDecoder(100),
        nullable=True,
    )

    fa_description_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
    )

    fa_actor: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
    )

    fa_timestamp: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
    )

    fa_deleted: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        comment="Visibility of deleted revisions, bitfield",
    )

    fa_sha1: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment="sha1 hash of file content",
    )


class Filerevision(Base):
    """Map the ``filerevision`` table.

    Revisions of the files
    """

    __tablename__ = "filerevision"

    __table_args__ = (
        Index(
            "fr_actor_timestamp",
            "fr_actor",
            "fr_timestamp",
        ),
        Index(
            "fr_size",
            "fr_size",
        ),
        Index(
            "fr_timestamp",
            "fr_timestamp",
        ),
        Index(
            "fr_sha1",
            "fr_sha1",
        ),
        Index(
            "fr_file",
            "fr_file",
        ),
    )

    fr_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="primary key",
    )

    fr_file: Mapped[int] = mapped_column(
        BIGINT(unsigned=False),
        nullable=False,
        comment="FK to file_id",
    )

    fr_size: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="File size in bytes.",
    )

    fr_width: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="For images, width in pixels.",
    )

    fr_height: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="For images, height in pixels.",
    )

    fr_metadata: Mapped[str] = mapped_column(
        BinaryDecoder(16777215),
        nullable=False,
        comment=(
            "Extracted Exif metadata stored as a json array (new "
            "system) or serialized PHP array (old system). The json "
            "array can contain an address in the text table or "
            "external storage."
        ),
    )

    fr_bits: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="For images, bits per pixel if known.",
    )

    fr_description_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment=(
            "Foreign key to comment table, which contains the "
            "description field as entered by the uploader. This is "
            "displayed in image upload history and logs."
        ),
    )

    fr_actor: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="actor_id of the uploader.",
    )

    fr_timestamp: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment="Time of the upload.",
    )

    fr_sha1: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment="SHA-1 content hash in base-36",
    )

    fr_archive_name: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "Filename of the archived file. This is generally a "
            "timestamp and '!' prepended to the base name."
        ),
    )

    fr_deleted: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True),
        nullable=False,
        comment="Whether the file is deleted",
    )


class Filetypes(Base):
    """Map the ``filetypes`` table.

    Valid file types
    """

    __tablename__ = "filetypes"

    __table_args__ = (
        Index(
            "ft_media_mime",
            "ft_media_type",
            "ft_major_mime",
            "ft_minor_mime",
            unique=True,
        ),
    )

    ft_id: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="primary key",
    )

    ft_media_type: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Media type as defined by the MEDIATYPE_xxx constants",
    )

    ft_major_mime: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "major part of a MIME media type as defined by IANA see "
            "https://www.iana.org/assignments/media-types/ for "
            '"chemical" cf. http://dx.doi.org/10.1021/ci9803233 by '
            "the ACS"
        ),
    )

    ft_minor_mime: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "minor part of a MIME media type as defined by IANA the "
            "minor parts are not required to adhere to any standard "
            "but should be consistent throughout the database see "
            "https://www.iana.org/assignments/media-types/"
        ),
    )


class Image(Base):
    """Map the ``image`` table.

    Uploaded images and other files.
    """

    __tablename__ = "image"

    __table_args__ = (
        Index(
            "img_actor_timestamp",
            "img_actor",
            "img_timestamp",
        ),
        Index(
            "img_size",
            "img_size",
        ),
        Index(
            "img_timestamp",
            "img_timestamp",
        ),
        Index(
            "img_sha1",
            "img_sha1",
        ),
        Index(
            "img_media_mime",
            "img_media_type",
            "img_major_mime",
            "img_minor_mime",
        ),
    )

    img_name: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        primary_key=True,
        nullable=False,
        comment=(
            "Filename. This is also the title of the associated "
            "description page, which will be in namespace 6 "
            "(NS_FILE)."
        ),
    )

    img_size: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="File size in bytes.",
    )

    img_width: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment="For images, width in pixels.",
    )

    img_height: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment="For images, height in pixels.",
    )

    img_metadata: Mapped[str] = mapped_column(
        BinaryDecoder(16777215),
        nullable=False,
        comment=(
            "Extracted Exif metadata stored as a json array (new "
            "system) or serialized PHP array (old system). The json "
            "array can contain an address in the text table or "
            "external storage."
        ),
    )

    img_bits: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment="For images, bits per pixel if known.",
    )

    img_media_type: Mapped[str | None] = mapped_column(
        EnumDecoder(
            "UNKNOWN",
            "BITMAP",
            "DRAWING",
            "AUDIO",
            "VIDEO",
            "MULTIMEDIA",
            "OFFICE",
            "TEXT",
            "EXECUTABLE",
            "ARCHIVE",
            "3D",
        ),
        nullable=True,
        comment="Media type as defined by the MEDIATYPE_xxx constants",
    )

    img_major_mime: Mapped[str] = mapped_column(
        EnumDecoder(
            "unknown",
            "application",
            "audio",
            "image",
            "text",
            "video",
            "message",
            "model",
            "multipart",
            "chemical",
        ),
        nullable=False,
        comment=(
            "major part of a MIME media type as defined by IANA see "
            "https://www.iana.org/assignments/media-types/ for "
            '"chemical" cf. http://dx.doi.org/10.1021/ci9803233 by '
            "the ACS"
        ),
    )

    img_minor_mime: Mapped[str] = mapped_column(
        BinaryDecoder(100),
        nullable=False,
        comment=(
            "minor part of a MIME media type as defined by IANA the "
            "minor parts are not required to adhere to any standard "
            "but should be consistent throughout the database see "
            "https://www.iana.org/assignments/media-types/"
        ),
    )

    img_description_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment=(
            "Foreign key to comment table, which contains the "
            "description field as entered by the uploader. This is "
            "displayed in image upload history and logs."
        ),
    )

    img_actor: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="actor_id of the uploader.",
    )

    img_timestamp: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment="Time of the upload.",
    )

    img_sha1: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment="SHA-1 content hash in base-36",
    )


class Imagelinks(Base):
    """Map the ``imagelinks`` table.

    Track links to images *used inline* We don't distinguish live from
    broken links here, so they do not need to be changed on
    upload/removal.
    """

    __tablename__ = "imagelinks"

    __table_args__ = (
        Index(
            "il_to",
            "il_to",
            "il_from",
        ),
        Index(
            "il_backlinks_namespace",
            "il_from_namespace",
            "il_to",
            "il_from",
        ),
        Index(
            "il_target_id",
            "il_target_id",
            "il_from",
        ),
        Index(
            "il_backlinks_namespace_target_id",
            "il_from_namespace",
            "il_target_id",
            "il_from",
        ),
    )

    il_from: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment=(
            "Key to page_id of the page containing the image / media link."
        ),
    )

    il_from_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment="Namespace for this page",
    )

    il_to: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "Filename of target image. This is also the page_title of "
            "the file's description page; all such pages are in "
            "namespace 6 (NS_FILE)."
        ),
    )

    il_target_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Foreign key to linktarget.lt_id",
    )


class Interwiki(Base):
    """Map the ``interwiki`` table.

    Recognized interwiki link prefixes
    """

    __tablename__ = "interwiki"

    iw_prefix: Mapped[str] = mapped_column(
        StringDecoder(32),
        primary_key=True,
        nullable=False,
        comment=(
            'The interwiki prefix, (e.g. "Meatball", or the language '
            'prefix "de")'
        ),
    )

    iw_url: Mapped[str] = mapped_column(
        BinaryDecoder(65530),
        nullable=False,
        comment=(
            'The URL of the wiki, with "$1" as a placeholder for an '
            "article name. Any spaces in the name will be transformed "
            "to underscores before insertion."
        ),
    )

    iw_api: Mapped[str] = mapped_column(
        BinaryDecoder(65530),
        nullable=False,
        comment="The URL of the file api.php",
    )

    iw_wikiid: Mapped[str] = mapped_column(
        StringDecoder(64),
        nullable=False,
        comment=(
            "The name of the database (for a connection to be "
            "established with LBFactory::getMainLB( 'wikiid' ))"
        ),
    )

    iw_local: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment=(
            "A boolean value indicating whether the wiki is in this "
            "project (used, for example, to detect redirect loops)"
        ),
    )

    iw_trans: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment=(
            "Boolean value indicating whether interwiki transclusions "
            "are allowed."
        ),
    )


class IpChanges(Base):
    """Map the ``ip_changes`` table.

    Every time an edit by a logged out user is saved, a row is created
    in ip_changes. This stores the IP as a hex representation so that we
    can more easily find edits within an IP range.
    """

    __tablename__ = "ip_changes"

    __table_args__ = (
        Index(
            "ipc_rev_timestamp",
            "ipc_rev_timestamp",
        ),
        Index(
            "ipc_hex_time",
            "ipc_hex",
            "ipc_rev_timestamp",
        ),
    )

    ipc_rev_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment=(
            "Foreign key to the revision table, also serves as the "
            "unique primary key"
        ),
    )

    ipc_rev_timestamp: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment="The timestamp of the revision",
    )

    ipc_hex: Mapped[str] = mapped_column(
        BinaryDecoder(35),
        nullable=False,
        comment=(
            "Hex representation of the IP address, as returned by "
            "Wikimedia\\IPUtils::toHex() For IPv4 it will resemble: "
            "ABCD1234 For IPv6: v6-ABCD1234000000000000000000000000 "
            "BETWEEN is then used to identify revisions within a "
            "given range"
        ),
    )


class IpblocksRestrictions(Base):
    """Map the ``ipblocks_restrictions`` table.

    Partial Block Restrictions
    """

    __tablename__ = "ipblocks_restrictions"

    __table_args__ = (
        Index(
            "ir_type_value",
            "ir_type",
            "ir_value",
        ),
    )

    ir_ipb_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="The bl_id from block",
    )

    ir_type: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        primary_key=True,
        nullable=False,
        comment="The restriction type id.",
    )

    ir_value: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment=(
            "The restriction id that corresponds to the type: page "
            "ID, namespace ID or BlockActionInfo ID."
        ),
    )


class Iwlinks(Base):
    """Map the ``iwlinks`` table.

    Track inline interwiki links
    """

    __tablename__ = "iwlinks"

    __table_args__ = (
        Index(
            "iwl_prefix_title_from",
            "iwl_prefix",
            "iwl_title",
            "iwl_from",
        ),
    )

    iwl_from: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="page_id of the referring page",
    )

    iwl_prefix: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        primary_key=True,
        nullable=False,
        comment="Interwiki prefix code of the target",
    )

    iwl_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        primary_key=True,
        nullable=False,
        comment="Title of the target, including namespace",
    )


class Job(Base):
    """Map the ``job`` table.

    Jobs performed by parallel apache threads or a command-line daemon
    """

    __tablename__ = "job"

    __table_args__ = (
        Index(
            "job_sha1",
            "job_sha1",
        ),
        Index(
            "job_cmd_token",
            "job_cmd",
            "job_token",
            "job_random",
        ),
        Index(
            "job_cmd_token_id",
            "job_cmd",
            "job_token",
            "job_id",
        ),
        Index(
            "job_cmd",
            "job_cmd",
            "job_namespace",
            "job_title",
            "job_params",
        ),
        Index(
            "job_timestamp",
            "job_timestamp",
        ),
    )

    job_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    job_cmd: Mapped[str] = mapped_column(
        BinaryDecoder(60),
        nullable=False,
        comment="Command name. Limited to 60 to prevent key length overflow",
    )

    job_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment=(
            "Namespace to act on. Should be 0 if the command does not "
            "operate on a title"
        ),
    )

    job_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "Title to act on. Should be '' if the command does not "
            "operate on a title"
        ),
    )

    job_timestamp: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
        comment=(
            "Timestamp of when the job was inserted. NULL for jobs "
            "added before addition of the timestamp"
        ),
    )

    job_params: Mapped[str] = mapped_column(
        BinaryDecoder(16777215),
        nullable=False,
        comment=(
            "Any other parameters to the command. Stored as a PHP "
            "serialized array, or an empty string if there are no "
            "parameters"
        ),
    )

    job_random: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment=(
            "Random, non-unique, number used for job acquisition (for "
            "lock concurrency)"
        ),
    )

    job_attempts: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="The number of times this job has been locked",
    )

    job_token: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment="Field that conveys process locks on rows via process UUIDs",
    )

    job_token_timestamp: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
        comment="Timestamp when the job was locked",
    )

    job_sha1: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment=(
            "Base 36 SHA1 of the job parameters relevant to detecting "
            "duplicates"
        ),
    )


class L10nCache(Base):
    """Map the ``l10n_cache`` table.

    Table for storing localisation data
    """

    __tablename__ = "l10n_cache"

    lc_lang: Mapped[str] = mapped_column(
        BinaryDecoder(35),
        primary_key=True,
        nullable=False,
        comment="Language code",
    )

    lc_key: Mapped[str] = mapped_column(
        StringDecoder(255),
        primary_key=True,
        nullable=False,
        comment="Cache key",
    )

    lc_value: Mapped[str] = mapped_column(
        BinaryDecoder(16777215),
        nullable=False,
        comment="Value",
    )


class Langlinks(Base):
    """Map the ``langlinks`` table.

    Track interlanguage links.
    """

    __tablename__ = "langlinks"

    __table_args__ = (
        Index(
            "ll_lang",
            "ll_lang",
            "ll_title",
        ),
    )

    ll_from: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="page_id of the referring page",
    )

    ll_lang: Mapped[str] = mapped_column(
        BinaryDecoder(35),
        primary_key=True,
        nullable=False,
        comment="Language code of the target",
    )

    ll_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Title of the target, including namespace",
    )


class Linktarget(Base):
    """Map the ``linktarget`` table.

    Holds immutable records of link targets, used mostly for links
    tables.
    """

    __tablename__ = "linktarget"

    __table_args__ = (
        Index(
            "lt_namespace_title",
            "lt_namespace",
            "lt_title",
            unique=True,
        ),
    )

    lt_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="primary key",
    )

    lt_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment="Namespace of the link target",
    )

    lt_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Text part of link target excluding namespace",
    )


class LogSearch(Base):
    """Map the ``log_search`` table."""

    __tablename__ = "log_search"

    __table_args__ = (
        Index(
            "ls_log_id",
            "ls_log_id",
        ),
    )

    ls_field: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        primary_key=True,
        nullable=False,
        comment="The type of ID (rev ID, log ID, rev timestamp, username)",
    )

    ls_value: Mapped[str] = mapped_column(
        StringDecoder(255),
        primary_key=True,
        nullable=False,
        comment="The value of the ID",
    )

    ls_log_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Key to log_id",
    )


class Logging(Base):
    """Map the ``logging`` table."""

    __tablename__ = "logging"

    __table_args__ = (
        Index(
            "log_type_time",
            "log_type",
            "log_timestamp",
        ),
        Index(
            "log_actor_time",
            "log_actor",
            "log_timestamp",
        ),
        Index(
            "log_page_time",
            "log_namespace",
            "log_title",
            "log_timestamp",
        ),
        Index(
            "log_times",
            "log_timestamp",
        ),
        Index(
            "log_actor_type_time",
            "log_actor",
            "log_type",
            "log_timestamp",
        ),
        Index(
            "log_page_id_time",
            "log_page",
            "log_timestamp",
        ),
        Index(
            "log_type_action",
            "log_type",
            "log_action",
            "log_timestamp",
        ),
    )

    log_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment=(
            "Log ID, for referring to this specific log entry, "
            "probably for deletion and such."
        ),
    )

    log_type: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment=(
            "Symbolic key for the general log type. The output format "
            "will be controlled by the log_action field."
        ),
    )

    log_action: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment="Symbolic key for the log action type.",
    )

    log_timestamp: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
    )

    log_actor: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
    )

    log_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment="Key to the namespace of the page affected",
    )

    log_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Key to the title of the page affected",
    )

    log_page: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment="Key to the page affected",
    )

    log_comment_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="Key to comment_id. Comment summarizing the change.",
    )

    log_params: Mapped[str] = mapped_column(
        BinaryDecoder(65530),
        nullable=False,
        comment=(
            "LF separated list (old system) or serialized PHP array "
            "(new system)"
        ),
    )

    log_deleted: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        comment="rev_deleted for logs",
    )


class Objectcache(Base):
    """Map the ``objectcache`` table.

    For a few generic cache operations if not using Memcached
    """

    __tablename__ = "objectcache"

    __table_args__ = (
        Index(
            "exptime",
            "exptime",
        ),
    )

    keyname: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        primary_key=True,
        nullable=False,
    )

    value: Mapped[str | None] = mapped_column(
        BinaryDecoder(16777215),
        nullable=True,
    )

    exptime: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
    )


class Oldimage(Base):
    """Map the ``oldimage`` table.

    Previous revisions of uploaded files. Awkwardly, image rows have to
    be moved into this table at re-upload time.
    """

    __tablename__ = "oldimage"

    __table_args__ = (
        Index(
            "oi_actor_timestamp",
            "oi_actor",
            "oi_timestamp",
        ),
        Index(
            "oi_name_timestamp",
            "oi_name",
            "oi_timestamp",
        ),
        Index(
            "oi_name_archive_name",
            "oi_name",
            "oi_archive_name",
        ),
        Index(
            "oi_sha1",
            "oi_sha1",
        ),
        Index(
            "oi_timestamp",
            "oi_timestamp",
        ),
    )

    oi_name: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Base filename: key to image.img_name",
    )

    oi_archive_name: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "Filename of the archived file. This is generally a "
            "timestamp and '!' prepended to the base name."
        ),
    )

    oi_size: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
    )

    oi_width: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
    )

    oi_height: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
    )

    oi_bits: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
    )

    oi_description_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
    )

    oi_actor: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
    )

    oi_timestamp: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
    )

    oi_metadata: Mapped[str] = mapped_column(
        BinaryDecoder(16777215),
        nullable=False,
    )

    oi_media_type: Mapped[str | None] = mapped_column(
        EnumDecoder(
            "UNKNOWN",
            "BITMAP",
            "DRAWING",
            "AUDIO",
            "VIDEO",
            "MULTIMEDIA",
            "OFFICE",
            "TEXT",
            "EXECUTABLE",
            "ARCHIVE",
            "3D",
        ),
        nullable=True,
    )

    oi_major_mime: Mapped[str] = mapped_column(
        EnumDecoder(
            "unknown",
            "application",
            "audio",
            "image",
            "text",
            "video",
            "message",
            "model",
            "multipart",
            "chemical",
        ),
        nullable=False,
    )

    oi_minor_mime: Mapped[str] = mapped_column(
        BinaryDecoder(100),
        nullable=False,
    )

    oi_deleted: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
    )

    oi_sha1: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
    )

    __mapper_args__ = {  # ruff: ignore[mutable-class-default]
        "primary_key": (
            oi_name,
            oi_archive_name,
        ),
    }


class Page(Base):
    """Map the ``page`` table.

    Core of the wiki: each page has an entry here which identifies it by
    title and contains some essential metadata.
    """

    __tablename__ = "page"

    __table_args__ = (
        Index(
            "page_name_title",
            "page_namespace",
            "page_title",
            unique=True,
        ),
        Index(
            "page_random",
            "page_random",
        ),
        Index(
            "page_len",
            "page_len",
        ),
        Index(
            "page_redirect_namespace_len",
            "page_is_redirect",
            "page_namespace",
            "page_len",
        ),
    )

    page_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment=(
            "Unique identifier number. The page_id will be preserved "
            "across edits and rename operations, but not deletions "
            "and recreations."
        ),
    )

    page_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment=(
            "A page name is broken into a namespace and a title. The "
            "namespace keys are UI-language-independent constants, "
            "defined in includes/Defines.php"
        ),
    )

    page_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "The rest of the title, as text. Spaces are transformed "
            "into underscores in title storage."
        ),
    )

    page_is_redirect: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        comment=(
            "1 indicates the article is a redirect. If so, there is a "
            "row in the `redirect` table with rd_from=page_id, which "
            "contains the redirect target."
        ),
    )

    page_is_new: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        comment=(
            "1 indicates this is a new entry, with only one edit. Not "
            "all pages with one edit are new pages."
        ),
    )

    page_random: Mapped[float] = mapped_column(
        FLOAT(unsigned=True),
        nullable=False,
        comment="Random value between 0 and 1, used for Special:Randompage",
    )

    page_touched: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment=(
            "This timestamp is updated whenever the page changes in a "
            "way requiring it to be re-rendered, invalidating caches. "
            "Aside from editing this includes permission changes, "
            "creation or deletion of linked pages, and alteration of "
            "contained templates."
        ),
    )

    page_links_updated: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
        comment=(
            "This timestamp is updated whenever a page is re-parsed "
            "and it has all the link tracking tables updated for it. "
            "This is useful for de-duplicating expensive backlink "
            "update jobs."
        ),
    )

    page_latest: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment=(
            "Handy key to revision.rev_id of the current revision. "
            "This may be 0 during page creation, but that shouldn't "
            "happen outside of a transaction... hopefully."
        ),
    )

    page_len: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment=(
            "Uncompressed length in bytes of the page's current source text."
        ),
    )

    page_content_model: Mapped[str | None] = mapped_column(
        BinaryDecoder(32),
        nullable=True,
        comment="content model, see CONTENT_MODEL_XXX constants",
    )

    page_lang: Mapped[str | None] = mapped_column(
        BinaryDecoder(35),
        nullable=True,
        comment="Page content language",
    )


class PageProps(Base):
    """Map the ``page_props`` table.

    Name/value pairs indexed by page_id
    """

    __tablename__ = "page_props"

    __table_args__ = (
        Index(
            "pp_propname_page",
            "pp_propname",
            "pp_page",
            unique=True,
        ),
        Index(
            "pp_propname_sortkey_page",
            "pp_propname",
            "pp_sortkey",
            "pp_page",
            unique=True,
        ),
    )

    pp_page: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
    )

    pp_propname: Mapped[str] = mapped_column(
        BinaryDecoder(60),
        primary_key=True,
        nullable=False,
    )

    pp_value: Mapped[str] = mapped_column(
        BinaryDecoder(65530),
        nullable=False,
    )

    pp_sortkey: Mapped[float | None] = mapped_column(
        FLOAT(unsigned=False),
        nullable=True,
    )


class PageRestrictions(Base):
    """Map the ``page_restrictions`` table.

    Used for storing page restrictions (i.e. protection levels)
    """

    __tablename__ = "page_restrictions"

    __table_args__ = (
        Index(
            "pr_pagetype",
            "pr_page",
            "pr_type",
            unique=True,
        ),
        Index(
            "pr_typelevel",
            "pr_type",
            "pr_level",
        ),
        Index(
            "pr_level",
            "pr_level",
        ),
        Index(
            "pr_cascade",
            "pr_cascade",
        ),
    )

    pr_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment=(
            "Field for an ID for this restrictions row (sort-key for "
            "Special:ProtectedPages)"
        ),
    )

    pr_page: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="Page to apply restrictions to (Foreign Key to page).",
    )

    pr_type: Mapped[str] = mapped_column(
        BinaryDecoder(60),
        nullable=False,
        comment="The protection type (edit, move, etc)",
    )

    pr_level: Mapped[str] = mapped_column(
        BinaryDecoder(60),
        nullable=False,
        comment="The protection level (Sysop, autoconfirmed, etc)",
    )

    pr_cascade: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
    )

    pr_expiry: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
        comment="Field for time-limited protection.",
    )


class Pagelinks(Base):
    """Map the ``pagelinks`` table.

    Track page-to-page hyperlinks within the wiki. The target page may
    or may not exist, and due to renames and deletions may refer to
    different page records as time goes by.
    """

    __tablename__ = "pagelinks"

    __table_args__ = (
        Index(
            "pl_target_id",
            "pl_target_id",
            "pl_from",
        ),
        Index(
            "pl_backlinks_namespace_target_id",
            "pl_from_namespace",
            "pl_target_id",
            "pl_from",
        ),
    )

    pl_from: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Key to the page_id of the page containing the link.",
    )

    pl_from_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment="Namespace for pl_from page",
    )

    pl_target_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=True,
        comment="Foreign key to linktarget.lt_id",
    )


class ProtectedTitles(Base):
    """Map the ``protected_titles`` table.

    Used for storing nonexistent pages that have been protected
    """

    __tablename__ = "protected_titles"

    __table_args__ = (
        Index(
            "pt_timestamp",
            "pt_timestamp",
        ),
    )

    pt_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        primary_key=True,
        nullable=False,
    )

    pt_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        primary_key=True,
        nullable=False,
    )

    pt_user: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
    )

    pt_reason_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
    )

    pt_timestamp: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
    )

    pt_expiry: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
    )

    pt_create_perm: Mapped[str] = mapped_column(
        BinaryDecoder(60),
        nullable=False,
    )


class Querycache(Base):
    """Map the ``querycache`` table.

    Used for caching expensive grouped queries
    """

    __tablename__ = "querycache"

    __table_args__ = (
        Index(
            "qc_type",
            "qc_type",
            "qc_value",
        ),
    )

    qc_type: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment="A key name, generally the base name of the special page",
    )

    qc_value: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="Some sort of stored value. Sizes, counts...",
    )

    qc_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
    )

    qc_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
    )

    __mapper_args__ = {  # ruff: ignore[mutable-class-default]
        "primary_key": (
            qc_type,
            qc_value,
            qc_namespace,
            qc_title,
        ),
    }


class QuerycacheInfo(Base):
    """Map the ``querycache_info`` table.

    Details of updates to cached special pages
    """

    __tablename__ = "querycache_info"

    qci_type: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        primary_key=True,
        nullable=False,
        comment="Special page name. Corresponds to a qc_type value",
    )

    qci_timestamp: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment="Timestamp of last update",
    )


class Querycachetwo(Base):
    """Map the ``querycachetwo`` table.

    Used for caching expensive grouped queries that need two links (for
    example double-redirects)
    """

    __tablename__ = "querycachetwo"

    __table_args__ = (
        Index(
            "qcc_type",
            "qcc_type",
            "qcc_value",
        ),
        Index(
            "qcc_title",
            "qcc_type",
            "qcc_namespace",
            "qcc_title",
        ),
        Index(
            "qcc_titletwo",
            "qcc_type",
            "qcc_namespacetwo",
            "qcc_titletwo",
        ),
    )

    qcc_type: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment="A key name, generally the base name of the special page.",
    )

    qcc_value: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="Some sort of stored value. Sizes, counts...",
    )

    qcc_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
    )

    qcc_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
    )

    qcc_namespacetwo: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
    )

    qcc_titletwo: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
    )

    __mapper_args__ = {  # ruff: ignore[mutable-class-default]
        "primary_key": (
            qcc_type,
            qcc_value,
            qcc_namespace,
            qcc_title,
            qcc_namespacetwo,
            qcc_titletwo,
        ),
    }


class Recentchanges(Base):
    """Map the ``recentchanges`` table.

    Primarily a summary table for Special:RecentChanges, this table
    contains some additional info on edits from the last few days
    """

    __tablename__ = "recentchanges"

    __table_args__ = (
        Index(
            "rc_timestamp",
            "rc_timestamp",
        ),
        Index(
            "rc_namespace_title_timestamp",
            "rc_namespace",
            "rc_title",
            "rc_timestamp",
        ),
        Index(
            "rc_cur_id",
            "rc_cur_id",
        ),
        Index(
            "rc_source_name_timestamp",
            "rc_source",
            "rc_namespace",
            "rc_timestamp",
        ),
        Index(
            "rc_ip",
            "rc_ip",
        ),
        Index(
            "rc_ns_actor",
            "rc_namespace",
            "rc_actor",
        ),
        Index(
            "rc_actor",
            "rc_actor",
            "rc_timestamp",
        ),
        Index(
            "rc_name_source_patrolled_timestamp",
            "rc_namespace",
            "rc_source",
            "rc_patrolled",
            "rc_timestamp",
        ),
        Index(
            "rc_this_oldid",
            "rc_this_oldid",
        ),
    )

    rc_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    rc_timestamp: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
    )

    rc_actor: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="As in revision",
    )

    rc_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment="When pages are renamed, their RC entries do _not_ change.",
    )

    rc_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
    )

    rc_comment_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="as in revision...",
    )

    rc_minor: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
    )

    rc_bot: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        comment=(
            "Edits by user accounts with the 'bot' rights key are "
            "marked with a 1 here, and will be hidden from the "
            "default view."
        ),
    )

    rc_cur_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment=(
            "Key to page_id (was cur_id prior to 1.5). This will keep "
            "links working after moves while retaining the at-the- "
            "time name in the changes list."
        ),
    )

    rc_this_oldid: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment=(
            "rev_id of the changed revision, or zero if the change "
            "does not relate to a revision."
        ),
    )

    rc_last_oldid: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="rev_id of the prior revision, for generating diff links.",
    )

    rc_source: Mapped[str] = mapped_column(
        BinaryDecoder(16),
        nullable=False,
        comment="The source of the change entry",
    )

    rc_patrolled: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        comment=(
            "If the Recent Changes Patrol option is enabled, users "
            "may mark edits as having been reviewed to remove a "
            "warning flag on the RC list. A value of 1 indicates the "
            "page has been reviewed."
        ),
    )

    rc_ip: Mapped[str] = mapped_column(
        BinaryDecoder(40),
        nullable=False,
        comment=(
            "Recorded IP address the edit was made from, if the "
            "$wgPutIPinRC option is enabled."
        ),
    )

    rc_old_len: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=False),
        nullable=True,
        comment="Text length in characters before the edit",
    )

    rc_new_len: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=False),
        nullable=True,
        comment="Text length in characters after the edit",
    )

    rc_deleted: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        comment="Visibility of recent changes items, bitfield",
    )

    rc_logid: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="Value corresponding to log_id, specific log entries",
    )

    rc_log_type: Mapped[str | None] = mapped_column(
        BinaryDecoder(255),
        nullable=True,
        comment="Store log type info here, or null",
    )

    rc_log_action: Mapped[str | None] = mapped_column(
        BinaryDecoder(255),
        nullable=True,
        comment="Store log action or null",
    )

    rc_params: Mapped[str | None] = mapped_column(
        BinaryDecoder(65535),
        nullable=True,
        comment="Log params",
    )


class Redirect(Base):
    """Map the ``redirect`` table.

    For each redirect, this table contains exactly one row defining its
    target. Redirect targets are key to page_namespace/page_title of the
    target page. The target page may or may not exist, and due to
    renames and deletions may refer to different page records as time
    goes by.
    """

    __tablename__ = "redirect"

    __table_args__ = (
        Index(
            "rd_ns_title",
            "rd_namespace",
            "rd_title",
            "rd_from",
        ),
    )

    rd_from: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Key to the page_id of the redirect page",
    )

    rd_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
    )

    rd_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
    )

    rd_interwiki: Mapped[str | None] = mapped_column(
        StringDecoder(32),
        nullable=True,
        comment=(
            "After T346290 all values have been computed and this "
            "field is never null. TODO: Mark as not null (T348029)."
        ),
    )

    rd_fragment: Mapped[str | None] = mapped_column(
        BinaryDecoder(255),
        nullable=True,
        comment=(
            "After T346290 all values have been computed and this "
            "field is never null. TODO: Mark as not null (T348029)."
        ),
    )


class Revision(Base):
    """Map the ``revision`` table.

    Every edit of a page creates also a revision row. This stores
    metadata about the revision, and a reference to the text storage
    backend.
    """

    __tablename__ = "revision"

    __table_args__ = (
        Index(
            "rev_timestamp",
            "rev_timestamp",
        ),
        Index(
            "rev_page_timestamp",
            "rev_page",
            "rev_timestamp",
        ),
        Index(
            "rev_actor_timestamp",
            "rev_actor",
            "rev_timestamp",
            "rev_id",
        ),
        Index(
            "rev_page_actor_timestamp",
            "rev_page",
            "rev_actor",
            "rev_timestamp",
        ),
    )

    rev_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Unique ID to identify each revision",
    )

    rev_page: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="Key to page_id. This should never be invalid",
    )

    rev_comment_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="Key to comment.comment_id. Comment summarizing the change",
    )

    rev_actor: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="Key to actor.actor_id of the user or IP who made this edit",
    )

    rev_timestamp: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment="Timestamp of when revision was created",
    )

    rev_minor_edit: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        comment=(
            "Records whether the user marked the 'minor edit' "
            "checkbox. Many automated edits are marked as minor"
        ),
    )

    rev_deleted: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        comment="Restrictions on who can access this revision",
    )

    rev_len: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment="Length of this revision in bytes",
    )

    rev_parent_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        comment=(
            "Key to revision.rev_id. This field is used to add "
            "support for a tree structure (The Adjacency List Model)"
        ),
    )


class Searchindex(Base):
    """Map the ``searchindex`` table.

    search backend, this is actively used in MySQL but created and not
    used in Postgres while there are plans to use it in the future
    (T220450). This table must be MyISAM in MySQL; InnoDB does not
    support the needed fulltext index.
    """

    __tablename__ = "searchindex"

    __table_args__ = (
        Index(
            "si_title",
            "si_title",
        ),
        Index(
            "si_text",
            "si_text",
        ),
    )

    si_page: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Key to page_id",
    )

    si_title: Mapped[str] = mapped_column(
        TextDecoder(16777215),
        nullable=False,
        comment="Munged version of title",
    )

    si_text: Mapped[str] = mapped_column(
        TextDecoder(16777215),
        nullable=False,
        comment="Munged version of body text",
    )


class SiteIdentifiers(Base):
    """Map the ``site_identifiers`` table.

    Links local site identifiers to their corresponding site.
    """

    __tablename__ = "site_identifiers"

    __table_args__ = (
        Index(
            "si_site",
            "si_site",
        ),
        Index(
            "si_key",
            "si_key",
        ),
    )

    si_type: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        primary_key=True,
        nullable=False,
        comment="local key type, ie 'interwiki' or 'langlink'",
    )

    si_key: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        primary_key=True,
        nullable=False,
        comment="local key value, ie 'en' or 'wiktionary'",
    )

    si_site: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="Key on sites.site_id",
    )


class SiteStats(Base):
    """Map the ``site_stats`` table.

    Contains a single row with some aggregate info on the state of the
    site.
    """

    __tablename__ = "site_stats"

    ss_row_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="The single row should contain 1 here.",
    )

    ss_total_edits: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        comment="Total number of edits performed.",
    )

    ss_good_articles: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        comment="See SiteStatsInit::articles().",
    )

    ss_total_pages: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        comment=(
            "Total pages, theoretically equal to SELECT COUNT(*) FROM page."
        ),
    )

    ss_users: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        comment=(
            "Number of users, theoretically equal to SELECT COUNT(*) "
            "FROM user."
        ),
    )

    ss_active_users: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        comment="Number of users that still edit.",
    )

    ss_images: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        comment=(
            "Number of images, equivalent to SELECT COUNT(*) FROM image."
        ),
    )


class Sites(Base):
    """Map the ``sites`` table.

    Holds all the sites known to the wiki.
    """

    __tablename__ = "sites"

    __table_args__ = (
        Index(
            "site_global_key",
            "site_global_key",
            unique=True,
        ),
    )

    site_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Numeric id of the site",
    )

    site_global_key: Mapped[str] = mapped_column(
        BinaryDecoder(64),
        nullable=False,
        comment="Global identifier for the site, ie 'enwiktionary'",
    )

    site_type: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment="Type of the site, ie 'mediawiki'",
    )

    site_group: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment="Group of the site, ie 'wikipedia'",
    )

    site_source: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment=(
            "Source of the site data, ie 'local', 'wikidata', 'my- "
            "magical-repo'"
        ),
    )

    site_language: Mapped[str] = mapped_column(
        BinaryDecoder(35),
        nullable=False,
        comment="Language code of the sites primary language.",
    )

    site_protocol: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment=(
            "Protocol of the site, ie 'http://', 'irc://', '//'. This "
            "field is an index for lookups and is build from type "
            "specific data in site_data."
        ),
    )

    site_domain: Mapped[str] = mapped_column(
        StringDecoder(255),
        nullable=False,
        comment=(
            "Domain of the site in reverse order, ie "
            "'org.mediawiki.www.'. This field is an index for lookups "
            "and is build from type specific data in site_data."
        ),
    )

    site_data: Mapped[str] = mapped_column(
        BinaryDecoder(65530),
        nullable=False,
        comment="Type dependent site data.",
    )

    site_forward: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment=(
            "If site.tld/path/key:pageTitle should forward users to "
            'the page on the actual site, where "key" is the local '
            "identifier."
        ),
    )

    site_config: Mapped[str] = mapped_column(
        BinaryDecoder(65530),
        nullable=False,
        comment=(
            "Type dependent site config. For instance if template "
            "transclusion should be allowed if it's a MediaWiki."
        ),
    )


class SlotRoles(Base):
    """Map the ``slot_roles`` table.

    Normalization table for role names
    """

    __tablename__ = "slot_roles"

    __table_args__ = (
        Index(
            "role_name",
            "role_name",
            unique=True,
        ),
    )

    role_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    role_name: Mapped[str] = mapped_column(
        BinaryDecoder(64),
        nullable=False,
    )


class Slots(Base):
    """Map the ``slots`` table.

    Slots represent an n:m relation between revisions and content
    objects. A content object can have a specific "role" in one or more
    revisions. Each revision can have multiple content objects, each
    having a different role.
    """

    __tablename__ = "slots"

    __table_args__ = (
        Index(
            "slot_revision_origin_role",
            "slot_revision_id",
            "slot_origin",
            "slot_role_id",
        ),
    )

    slot_revision_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="reference to rev_id or ar_rev_id",
    )

    slot_role_id: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="reference to role_id",
    )

    slot_content_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="reference to content_id",
    )

    slot_origin: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment=(
            "The revision ID of the revision that originated the "
            "slot's content. To find revisions that changed slots, "
            "look for slot_origin = slot_revision_id. TODO: Is that "
            "actually true? Rollback seems to violate it by setting "
            "slot_origin to an older rev_id. Undeletions could result "
            "in the same situation."
        ),
    )


class Templatelinks(Base):
    """Map the ``templatelinks`` table.

    Track template inclusions. The target page may or may not exist, and
    due to renames and deletions may refer to different page records as
    time goes by.
    """

    __tablename__ = "templatelinks"

    __table_args__ = (
        Index(
            "tl_target_id",
            "tl_target_id",
            "tl_from",
        ),
        Index(
            "tl_backlinks_namespace_target_id",
            "tl_from_namespace",
            "tl_target_id",
            "tl_from",
        ),
    )

    tl_from: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Key to the page_id of the page containing the link.",
    )

    tl_from_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
        comment="Namespace for this page",
    )

    tl_target_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Foreign key to linktarget.lt_id",
    )


class Text(Base):
    """Map the ``text`` table.

    Holds text of individual page revisions. Field names are a holdover
    from the 'old' revisions table in MediaWiki 1.4 and earlier: an
    upgrade will transform that table into the 'text' table to minimize
    unnecessary churning and downtime. If upgrading, the other fields
    will be left unused. This table can also hold metadata of files if
    the metadata is too big to store in img_metadata, oi_metadata or
    fa_metadata.
    """

    __tablename__ = "text"

    old_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment=(
            "Unique text storage key number. Note that the 'oldid' "
            "parameter used in URLs does *not* refer to this number "
            "anymore, but to rev_id. content.content_address refers "
            "to this column. Also img_metadata, oi_metadata or "
            "fa_metadata can refer to this column when being used to "
            "store file metadata."
        ),
    )

    old_text: Mapped[str] = mapped_column(
        BinaryDecoder(16777215),
        nullable=False,
        comment=(
            "Depending on the contents of the old_flags field, the "
            "text may be convenient plain text, or it may be funkily "
            "encoded."
        ),
    )

    old_flags: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "Comma-separated list of flags: * gzip: text is "
            "compressed with PHP's gzdeflate() function. * utf-8: "
            "text was stored as UTF-8. If $wgLegacyEncoding option is "
            "on, rows *without* this flag will be converted to UTF-8 "
            "transparently at load time. Note that due to a bug in a "
            "maintenance script, this flag may have been stored as "
            "'utf8' in some cases (T18841). * object: text field "
            "contained a serialized PHP object. The object either "
            "contains multiple versions compressed together to "
            "achieve a better compression ratio, or it refers to "
            "another row where the text can be found. * external: "
            "text was stored in an external location specified by "
            "old_text. Any additional flags apply to the data stored "
            "at that URL, not the URL itself. The 'object' flag is "
            "*not* set for URLs of the form 'DB://cluster/id/itemid', "
            "because the external storage system itself decompresses "
            "these."
        ),
    )


class Updatelog(Base):
    """Map the ``updatelog`` table.

    A table to log updates, one text key row per update.
    """

    __tablename__ = "updatelog"

    ul_key: Mapped[str] = mapped_column(
        StringDecoder(255),
        primary_key=True,
        nullable=False,
    )

    ul_value: Mapped[str | None] = mapped_column(
        BinaryDecoder(65530),
        nullable=True,
    )


class Uploadstash(Base):
    """Map the ``uploadstash`` table.

    Store information about newly uploaded files before they're moved
    into the actual filestore
    """

    __tablename__ = "uploadstash"

    __table_args__ = (
        Index(
            "us_user",
            "us_user",
        ),
        Index(
            "us_key",
            "us_key",
            unique=True,
        ),
        Index(
            "us_timestamp",
            "us_timestamp",
        ),
    )

    us_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    us_user: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="the user who uploaded the file.",
    )

    us_key: Mapped[str] = mapped_column(
        StringDecoder(255),
        nullable=False,
        comment=(
            "file key. this is how applications actually search for "
            "the file. this might go away, or become the primary key."
        ),
    )

    us_orig_path: Mapped[str] = mapped_column(
        StringDecoder(255),
        nullable=False,
        comment="the original path",
    )

    us_path: Mapped[str] = mapped_column(
        StringDecoder(255),
        nullable=False,
        comment="the temporary path at which the file is actually stored",
    )

    us_source_type: Mapped[str | None] = mapped_column(
        StringDecoder(50),
        nullable=True,
        comment="which type of upload the file came from (sometimes)",
    )

    us_timestamp: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment="the date/time on which the file was added",
    )

    us_status: Mapped[str] = mapped_column(
        StringDecoder(50),
        nullable=False,
    )

    us_chunk_inx: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment=(
            "chunk counter starts at 0, current offset is stored in us_size"
        ),
    )

    us_props: Mapped[str | None] = mapped_column(
        BinaryDecoder(65530),
        nullable=True,
        comment="Serialized file properties from FSFile::getProps()",
    )

    us_size: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="file size in bytes",
    )

    us_sha1: Mapped[str] = mapped_column(
        StringDecoder(31),
        nullable=False,
        comment=(
            "this hash comes from FSFile::getSha1Base36(), and is 31 "
            "characters"
        ),
    )

    us_mime: Mapped[str | None] = mapped_column(
        StringDecoder(255),
        nullable=True,
    )

    us_media_type: Mapped[str | None] = mapped_column(
        EnumDecoder(
            "UNKNOWN",
            "BITMAP",
            "DRAWING",
            "AUDIO",
            "VIDEO",
            "MULTIMEDIA",
            "OFFICE",
            "TEXT",
            "EXECUTABLE",
            "ARCHIVE",
            "3D",
        ),
        nullable=True,
        comment=(
            "Media type as defined by the MEDIATYPE_xxx constants, "
            "should duplicate definition in the image table"
        ),
    )

    us_image_width: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment="image-specific properties",
    )

    us_image_height: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
    )

    us_image_bits: Mapped[int | None] = mapped_column(
        SMALLINT(unsigned=True),
        nullable=True,
    )


class User(Base):
    """Map the ``user`` table.

    The user table contains basic account information, authentication
    keys, etc. Some multi-wiki sites may share a single central user
    table between separate wikis using the $wgSharedDB setting. Note
    that even when an external authentication plugin is in use, user
    table entries still need to be created to store preferences and to
    key tracking information in the other tables
    """

    __tablename__ = "user"

    __table_args__ = (
        Index(
            "user_name",
            "user_name",
            unique=True,
        ),
        Index(
            "user_email_token",
            "user_email_token",
        ),
        Index(
            "user_email",
            "user_email",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    user_name: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "Usernames must be unique, must not be in the form of an "
            "IP address. They should not allow slashes or case "
            "conflicts. Spaces are allowed, and are not converted to "
            "underscores like in page titles."
        ),
    )

    user_real_name: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Optional 'real name' to be displayed in credit listings",
    )

    user_password: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Password hashes",
    )

    user_newpassword: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment=(
            "When using 'mail me a new password', a random password "
            "is generated and the hash stored here. The previous "
            "password is left in place until someone actually logs in "
            "with the new password, at which point the hash is moved "
            "to user_password and the old password is invalidated."
        ),
    )

    user_newpass_time: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
        comment=(
            "Timestamp of the last time when a new password was sent, "
            "for throttling and expiring purposes. Emailed passwords "
            "will expire $wgNewPasswordExpiry (a week) after being "
            "set. If user_newpass_time is NULL (eg. created by mail) "
            "it doesn't expire."
        ),
    )

    user_email: Mapped[str] = mapped_column(
        TextDecoder(255),
        nullable=False,
        comment="User email. Non public info.",
    )

    user_touched: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment=(
            "If the browser sends an If-Modified-Since header, a 304 "
            "response is suppressed if the value in this field for "
            "the current user is later than the value in the IMS "
            "header. That is, this field is an invalidation timestamp "
            "for the browser cache of logged-in users. Among other "
            "things, it is used to prevent pages generated for a "
            "previously logged in user from being displayed after a "
            "session expiry followed by a fresh login."
        ),
    )

    user_token: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
        comment=(
            "A pseudorandomly generated value that is stored in a "
            "cookie when the 'remember password' feature is used "
            "(previously, a hash of the password was used, but this "
            "was vulnerable to cookie-stealing attacks)"
        ),
    )

    user_email_authenticated: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
        comment=(
            "Initially NULL; when a user's e-mail address has been "
            "validated by returning with a mailed token, this is set "
            "to the current timestamp."
        ),
    )

    user_email_token: Mapped[str | None] = mapped_column(
        BinaryDecoder(32),
        nullable=True,
        comment=(
            "Randomly generated token created when the e-mail address "
            "is set and a confirmation test mail sent."
        ),
    )

    user_email_token_expires: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
        comment="Expiration date for the user_email_token.",
    )

    user_registration: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
        comment=(
            "Timestamp of account registration. Accounts predating "
            "this schema addition may contain NULL."
        ),
    )

    user_editcount: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment=(
            "Count of edits and edit-like actions. Not intended to be "
            "an accurate copy of 'COUNT(*) WHERE rev_actor refers to "
            "a user's actor_id'. May contain NULL for old accounts if "
            "batch-update scripts haven't been run, as well as "
            "listing deleted edits and other myriad ways it could be "
            "out of sync. Meant primarily for heuristic checks to "
            "give an impression of whether the account has been used "
            "much."
        ),
    )

    user_password_expires: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
        comment="Expiration date for user password.",
    )

    user_is_temp: Mapped[int] = mapped_column(
        TINYINT(unsigned=False),
        nullable=False,
        comment=(
            "This exists to allow temporary users to be identified "
            "from the database only, by external applications, and is "
            "not for use within MediaWiki (see T333223). A boolean "
            "value representing whether the user is a temporary user. "
            "Zero if any type of user other than a temporary user."
        ),
    )


class UserAutocreateSerial(Base):
    """Map the ``user_autocreate_serial`` table.

    Table for sequential name generation for auto-created temporary
    users
    """

    __tablename__ = "user_autocreate_serial"

    uas_shard: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="The segment of ID space, ID mod N, referred to by this row",
    )

    uas_year: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True),
        primary_key=True,
        nullable=False,
        comment=(
            "The year to which this row belongs, if "
            "$wgAutoCreateTempUser['serialProvider']['useYear'] is "
            "true."
        ),
    )

    uas_value: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="The maximum allocated ID value",
    )


class UserFormerGroups(Base):
    """Map the ``user_former_groups`` table.

    Stores the groups the user has once belonged to. The user may still
    belong to these groups (check user_groups). Autopromotion of users
    to groups from which they were removed can be restricted by using
    wgAutopromoteOnce instead of wgAutopromote.
    """

    __tablename__ = "user_former_groups"

    ufg_user: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Key to user_id",
    )

    ufg_group: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        primary_key=True,
        nullable=False,
    )


class UserGroups(Base):
    """Map the ``user_groups`` table.

    User permissions have been broken out to a separate table; this
    allows sites with a shared user table to have different permissions
    assigned to a user in each project. This table replaces the old
    user_rights field which used a comma-separated blob.
    """

    __tablename__ = "user_groups"

    __table_args__ = (
        Index(
            "ug_group",
            "ug_group",
        ),
        Index(
            "ug_expiry",
            "ug_expiry",
        ),
    )

    ug_user: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Key to user_id",
    )

    ug_group: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        primary_key=True,
        nullable=False,
        comment=(
            "Group names are short symbolic string keys. The set of "
            "group names is open-ended, though in practice only some "
            "predefined ones are likely to be used. At runtime "
            "$wgGroupPermissions will associate group keys with "
            "particular permissions. A user will have the combined "
            "permissions of any group they're explicitly in, plus the "
            "implicit '*' and 'user' groups."
        ),
    )

    ug_expiry: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
        comment=(
            "Time at which the user group membership will expire. Set "
            "to NULL for a non-expiring (infinite) membership."
        ),
    )


class UserNewtalk(Base):
    """Map the ``user_newtalk`` table.

    Stores notifications of user talk page changes, for the display of
    the 'you have new messages' box
    """

    __tablename__ = "user_newtalk"

    __table_args__ = (
        Index(
            "un_user_id",
            "user_id",
        ),
        Index(
            "un_user_ip",
            "user_ip",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
    )

    user_ip: Mapped[str] = mapped_column(
        BinaryDecoder(40),
        nullable=False,
        comment=(
            "If the user is an anonymous user their IP address is "
            "stored here since the user_id of 0 is ambiguous"
        ),
    )

    user_last_timestamp: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
        comment=(
            "The highest timestamp of revisions of the talk page "
            "viewed by this user"
        ),
    )

    __mapper_args__ = {  # ruff: ignore[mutable-class-default]
        "primary_key": (
            user_id,
            user_ip,
        ),
    }


class UserProperties(Base):
    """Map the ``user_properties`` table.

    User preferences and perhaps other fun stuff. :) Replaces the old
    user.user_options blob, with a couple nice properties: 1) We only
    store non-default settings, so changes to the defaults are now
    reflected for everybody, not just new accounts. 2) We can more
    easily do bulk lookups, statistics, or modifications of saved
    options since it's a sensible table structure.
    """

    __tablename__ = "user_properties"

    __table_args__ = (
        Index(
            "up_property",
            "up_property",
        ),
    )

    up_user: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Foreign key to user.user_id",
    )

    up_property: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        primary_key=True,
        nullable=False,
        comment=(
            "Name of the option being saved. This is indexed for bulk lookup."
        ),
    )

    up_value: Mapped[str | None] = mapped_column(
        BinaryDecoder(65530),
        nullable=True,
        comment="Property value as a string.",
    )


class Watchlist(Base):
    """Map the ``watchlist`` table."""

    __tablename__ = "watchlist"

    __table_args__ = (
        Index(
            "wl_user",
            "wl_user",
            "wl_namespace",
            "wl_title",
            unique=True,
        ),
        Index(
            "wl_namespace_title",
            "wl_namespace",
            "wl_title",
        ),
        Index(
            "wl_user_notificationtimestamp",
            "wl_user",
            "wl_notificationtimestamp",
        ),
    )

    wl_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    wl_user: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
    )

    wl_namespace: Mapped[int] = mapped_column(
        INTEGER(unsigned=False),
        nullable=False,
    )

    wl_title: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
    )

    wl_notificationtimestamp: Mapped[str | None] = mapped_column(
        BinaryDecoder(14),
        nullable=True,
        comment=(
            "The timestamp of the earliest unseen revision, or null "
            "if the current revision has been seen. Also the "
            "timestamp of the last watchlist notification email, if "
            "that feature is enabled, used to suppress consecutive "
            'emails for the same page. Used to show "updated" '
            'markers, in combination with the "seen" cache.'
        ),
    )


class WatchlistExpiry(Base):
    """Map the ``watchlist_expiry`` table.

    Allows setting an expiry for watchlist items.
    """

    __tablename__ = "watchlist_expiry"

    __table_args__ = (
        Index(
            "we_expiry",
            "we_expiry",
        ),
    )

    we_item: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Key to watchlist.wl_id",
    )

    we_expiry: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment="Expiry time",
    )


class WatchlistLabel(Base):
    """Map the ``watchlist_label`` table.

    Watchlist label names
    """

    __tablename__ = "watchlist_label"

    __table_args__ = (
        Index(
            "wll_user_name",
            "wll_user",
            "wll_name",
            unique=True,
        ),
    )

    wll_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Unique ID for row",
    )

    wll_user: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="user_id for label owner",
    )

    wll_name: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
        comment="Label text",
    )


class WatchlistLabelMember(Base):
    """Map the ``watchlist_label_member`` table.

    Label to watchlist item associations
    """

    __tablename__ = "watchlist_label_member"

    __table_args__ = (
        Index(
            "wlm_item",
            "wlm_item",
        ),
    )

    wlm_label: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Key to watchlist_label.wll_id",
    )

    wlm_item: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="Key to watchlist.wl_id",
    )


class PageAssessmentsProjects(Base):
    """Map the ``page_assessments_projects`` table.

    Add wikiprojects table
    """

    __tablename__ = "page_assessments_projects"

    __table_args__ = (
        Index(
            "pap_project_title",
            "pap_project_title",
            unique=True,
        ),
    )

    pap_project_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Generated ID of the project",
    )

    pap_project_title: Mapped[str | None] = mapped_column(
        StringDecoder(255),
        nullable=True,
        comment=(
            "Name of the project assessing the page. In the case of a "
            "subproject or task force, this will be a combination of "
            "the project and subproject name, e.g. Films/Korean "
            "cinema task force."
        ),
    )

    pap_parent_id: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        nullable=True,
        comment="ID of the parent project (for subprojects and task forces)",
    )


class PageAssessments(Base):
    """Map the ``page_assessments`` table.

    Add article assessments table
    """

    __tablename__ = "page_assessments"

    __table_args__ = (
        Index(
            "pa_project",
            "pa_project_id",
            "pa_page_id",
        ),
    )

    pa_page_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="ID of the page",
    )

    pa_project_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        comment="ID of the project assessing the page",
    )

    pa_class: Mapped[str | None] = mapped_column(
        StringDecoder(20),
        nullable=True,
        comment="class of the page, e.g. 'B'",
    )

    pa_importance: Mapped[str | None] = mapped_column(
        StringDecoder(20),
        nullable=True,
        comment="importance of the page for the project, e.g. 'Low'",
    )

    pa_page_revision: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="revision of the page upon assessment",
    )


class WbtItemTerms(Base):
    """Map the ``wbt_item_terms`` table.

    Stores a record per term per item per language. this table is
    expected to be the longest one in this group of tables. Term text,
    type and language are normalized further through wb_term_in_lang
    table.
    """

    __tablename__ = "wbt_item_terms"

    __table_args__ = (
        Index(
            "wbt_item_terms_item_id",
            "wbit_item_id",
        ),
        Index(
            "wbt_item_terms_term_in_lang_id_item_id",
            "wbit_term_in_lang_id",
            "wbit_item_id",
            unique=True,
        ),
    )

    wbit_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    wbit_item_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
    )

    wbit_term_in_lang_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
    )


class WbtPropertyTerms(Base):
    """Map the ``wbt_property_terms`` table.

    Stores a record per term per property per language. Term text, type
    and language are normalized further through wb_term_in_lang table.
    """

    __tablename__ = "wbt_property_terms"

    __table_args__ = (
        Index(
            "wbt_property_terms_property_id",
            "wbpt_property_id",
        ),
        Index(
            "wbt_property_terms_term_in_lang_id_property_id",
            "wbpt_term_in_lang_id",
            "wbpt_property_id",
            unique=True,
        ),
    )

    wbpt_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    wbpt_property_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
    )

    wbpt_term_in_lang_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
    )


class WbtTermInLang(Base):
    """Map the ``wbt_term_in_lang`` table.

    Stores a record per term per text per language. Term text and
    language are normalized further through wb_text_in_lang table.
    """

    __tablename__ = "wbt_term_in_lang"

    __table_args__ = (
        Index(
            "wbt_term_in_lang_type_id_text_in",
            "wbtl_type_id",
        ),
        Index(
            "wbt_term_in_lang_text_in_lang_id_lang_id",
            "wbtl_text_in_lang_id",
            "wbtl_type_id",
            unique=True,
        ),
    )

    wbtl_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    wbtl_type_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
    )

    wbtl_text_in_lang_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
    )


class WbtTextInLang(Base):
    """Map the ``wbt_text_in_lang`` table.

    Stores a record per term text per language. Text is normalized
    through wb_term_text table.
    """

    __tablename__ = "wbt_text_in_lang"

    __table_args__ = (
        Index(
            "wbt_text_in_lang_language",
            "wbxl_language",
        ),
        Index(
            "wbt_text_in_lang_text_id_text_id",
            "wbxl_text_id",
            "wbxl_language",
            unique=True,
        ),
    )

    wbxl_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    wbxl_language: Mapped[str] = mapped_column(
        BinaryDecoder(20),
        nullable=False,
    )

    wbxl_text_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
    )


class WbtText(Base):
    """Map the ``wbt_text`` table.

    Stores a record per text value that are used in different terms in
    different languages.
    """

    __tablename__ = "wbt_text"

    __table_args__ = (
        Index(
            "wbt_text_text",
            "wbx_text",
            unique=True,
        ),
    )

    wbx_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    wbx_text: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
    )


class WbChanges(Base):
    """Map the ``wb_changes`` table.

    Change feed.
    """

    __tablename__ = "wb_changes"

    __table_args__ = (
        Index(
            "wb_changes_change_time",
            "change_time",
        ),
        Index(
            "wb_changes_change_revision_id",
            "change_revision_id",
        ),
        Index(
            "change_object_id",
            "change_object_id",
        ),
    )

    change_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
        comment="Id of change",
    )

    change_type: Mapped[str] = mapped_column(
        StringDecoder(25),
        nullable=False,
        comment="Type of the change",
    )

    change_time: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment="Time the change was made",
    )

    change_object_id: Mapped[str] = mapped_column(
        BinaryDecoder(14),
        nullable=False,
        comment=(
            "The full id of the object (ie item, query) the change affects"
        ),
    )

    change_revision_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="The id of the revision on the repo that made the change",
    )

    change_user_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="The id of the user on the repo that made the change",
    )

    change_info: Mapped[str] = mapped_column(
        BinaryDecoder(16777215),
        nullable=False,
        comment="Holds additional info about the change, inc diff and stuff",
    )


class WbChangesSubscription(Base):
    """Map the ``wb_changes_subscription`` table."""

    __tablename__ = "wb_changes_subscription"

    __table_args__ = (
        Index(
            "cs_entity_id",
            "cs_entity_id",
            "cs_subscriber_id",
            unique=True,
        ),
        Index(
            "cs_subscriber_id",
            "cs_subscriber_id",
        ),
    )

    cs_row_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=False),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    cs_entity_id: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
    )

    cs_subscriber_id: Mapped[str] = mapped_column(
        BinaryDecoder(255),
        nullable=False,
    )


class WbIdCounters(Base):
    """Map the ``wb_id_counters`` table.

    Unique ID generator.
    """

    __tablename__ = "wb_id_counters"

    id_value: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
    )

    id_type: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        primary_key=True,
        nullable=False,
    )


class WbItemsPerSite(Base):
    """Map the ``wb_items_per_site`` table.

    Derived storage. Links site+title pairs to item ids.
    """

    __tablename__ = "wb_items_per_site"

    __table_args__ = (
        Index(
            "wb_ips_item_site_page",
            "ips_site_id",
            "ips_site_page",
            unique=True,
        ),
        Index(
            "wb_ips_item_id",
            "ips_item_id",
        ),
    )

    ips_row_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    ips_item_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
    )

    ips_site_id: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
    )

    ips_site_page: Mapped[str] = mapped_column(
        StringDecoder(310),
        nullable=False,
    )


class WbPropertyInfo(Base):
    """Map the ``wb_property_info`` table."""

    __tablename__ = "wb_property_info"

    __table_args__ = (
        Index(
            "pi_type",
            "pi_type",
        ),
    )

    pi_property_id: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        primary_key=True,
        nullable=False,
    )

    pi_type: Mapped[str] = mapped_column(
        BinaryDecoder(32),
        nullable=False,
    )

    pi_info: Mapped[str] = mapped_column(
        BinaryDecoder(65535),
        nullable=False,
    )
