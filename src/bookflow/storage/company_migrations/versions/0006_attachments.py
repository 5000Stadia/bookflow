"""Add attachment metadata, links, size setting and collection intents."""

from alembic import op

revision = "co0006"
down_revision = "co0005"

DDL = (
    """ALTER TABLE company_info ADD COLUMN attachment_max_bytes INTEGER NOT NULL DEFAULT 25000000 CONSTRAINT ck_company_attachment_max_bytes CHECK (attachment_max_bytes BETWEEN 1 AND 100000000)""",
    """CREATE TABLE attachments (
    id VARCHAR(26) NOT NULL,
    version INTEGER NOT NULL,
    created_at VARCHAR(32) NOT NULL,
    created_by VARCHAR(26) NOT NULL,
    created_via VARCHAR(16) NOT NULL,
    updated_at VARCHAR(32) NOT NULL,
    updated_by VARCHAR(26) NOT NULL,
    updated_via VARCHAR(16) NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL,
    media_type VARCHAR(127) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    uploaded_by VARCHAR(26) NOT NULL,
    uploaded_at VARCHAR(32) NOT NULL,
    collected_at VARCHAR(32),
    PRIMARY KEY (id),
    CONSTRAINT ck_attachments_size CHECK (size_bytes >= 0),
    CONSTRAINT ck_attachments_sha256 CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    CONSTRAINT ck_attachments_filename CHECK (length(CAST(original_filename AS BLOB)) BETWEEN 1 AND 255),
    UNIQUE (sha256)
)""",
    """CREATE TABLE attachment_links (
    id VARCHAR(26) NOT NULL,
    version INTEGER NOT NULL,
    created_at VARCHAR(32) NOT NULL,
    created_by VARCHAR(26) NOT NULL,
    created_via VARCHAR(16) NOT NULL,
    updated_at VARCHAR(32) NOT NULL,
    updated_by VARCHAR(26) NOT NULL,
    updated_via VARCHAR(16) NOT NULL,
    attachment_id VARCHAR(26) NOT NULL,
    record_type VARCHAR(64) NOT NULL,
    record_id VARCHAR(26) NOT NULL,
    linked_by VARCHAR(26) NOT NULL,
    linked_at VARCHAR(32) NOT NULL,
    caption TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_attachment_links_caption CHECK (length(CAST(caption AS BLOB)) <= 2048),
    FOREIGN KEY(attachment_id) REFERENCES attachments (id) ON DELETE RESTRICT
)""",
    """CREATE INDEX ix_attachment_links_attachment_id ON attachment_links (attachment_id)""",
    """CREATE INDEX ix_attachment_links_target_id ON attachment_links (record_type, record_id, id)""",
    """CREATE UNIQUE INDEX uq_attachment_links_active ON attachment_links (attachment_id, record_type, record_id) WHERE active = 1""",
    """CREATE TABLE attachment_collection (
    id VARCHAR(26) NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_attachment_collection_payload CHECK (length(CAST(payload AS BLOB)) <= 262144 AND json_valid(payload))
)""",
)


def upgrade():
    for statement in DDL:
        op.execute(statement)

def downgrade():
    op.drop_table("attachment_collection")
    op.drop_table("attachment_links")
    op.drop_table("attachments")
    op.drop_column("company_info", "attachment_max_bytes")
