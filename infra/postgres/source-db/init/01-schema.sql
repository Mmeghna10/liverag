-- Northwind Gear application schema (the "source of truth").
-- LiveRAG never writes here; it only reads the WAL.

CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL UNIQUE,
    display_name  text NOT NULL,
    password_hash text NOT NULL,
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE groups (
    id   integer PRIMARY KEY,
    name text NOT NULL UNIQUE
);

CREATE TABLE user_groups (
    user_id  uuid    NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    group_id integer NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);

CREATE TABLE products (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sku         text NOT NULL UNIQUE,
    name        text NOT NULL,
    description text NOT NULL,
    price_inr   integer NOT NULL CHECK (price_inr >= 0),
    status      text NOT NULL DEFAULT 'in_stock'
                CHECK (status IN ('in_stock', 'low_stock', 'out_of_stock', 'discontinued')),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE documents (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind       text NOT NULL CHECK (kind IN ('article', 'policy')),
    title      text NOT NULL,
    body       text NOT NULL,
    is_public  boolean NOT NULL DEFAULT false,
    deleted_at timestamptz,               -- soft delete: set instead of DELETE
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE document_acl (
    document_id uuid    NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    group_id    integer NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
    PRIMARY KEY (document_id, group_id)
);

CREATE TABLE canary (
    id         integer PRIMARY KEY,
    value      bigint NOT NULL,
    written_at timestamptz NOT NULL DEFAULT now()
);

-- Keep updated_at honest on every UPDATE, whoever does it.
CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER products_set_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER documents_set_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();