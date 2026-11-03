-- Which tables' changes are streamed to LiveRAG.
-- users and groups are read directly by the API, not indexed.
CREATE PUBLICATION liverag_pub
    FOR TABLE products, documents, document_acl, user_groups, canary;