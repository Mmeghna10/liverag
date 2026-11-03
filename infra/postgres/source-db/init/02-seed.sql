-- Fixed reference data. Users, products and documents come from the generator (Step 3).
INSERT INTO groups (id, name) VALUES
    (1, 'support'),
    (2, 'sales'),
    (3, 'managers'),
    (4, 'hr'),
    (5, 'admins');

-- The single freshness-probe row; the canary writer will bump it every 10 s.
INSERT INTO canary (id, value) VALUES (1, 0);