"""Templates for Northwind Gear's synthetic data.

Content is driven by one seeded RNG, so every run produces the same rows.
IDs are derived from each record's natural key, so they never depend on RNG order.
"""

import random
import uuid
from dataclasses import dataclass, field

SEED = 42
PRODUCT_COUNT = 500
ARTICLE_COUNT = 300

GROUPS = {"support": 1, "sales": 2, "managers": 3, "hr": 4, "admins": 5}

# IDs come from each record's natural key (SKU, email, title), not from the RNG,
# so adding or removing data never changes another record's ID.
ID_NAMESPACE = uuid.UUID("6f1d3a52-6c1b-4c1e-9a57-2f0c9b7e4d10")


@dataclass(frozen=True)
class Category:
    code: str
    noun: str
    plural: str
    price_range: tuple[int, int]
    features: tuple[str, ...]
    return_days: int
    warranty_years: int


@dataclass
class Product:
    id: uuid.UUID
    sku: str
    name: str
    description: str
    price_inr: int
    status: str


@dataclass
class Document:
    id: uuid.UUID
    kind: str  # "article" or "policy"
    title: str
    body: str
    is_public: bool
    groups: list[str] = field(default_factory=list)


@dataclass
class User:
    id: uuid.UUID
    email: str
    display_name: str
    groups: list[str]


@dataclass
class Catalog:
    users: list[User]
    products: list[Product]
    articles: list[Document]
    policies: list[Document]


CATEGORIES = (
    Category("TNT", "Tent", "Tents", (4_999, 34_999),
             ("aluminium poles", "a 3,000 mm waterproof fly", "two vestibules",
              "a freestanding frame", "taped seams", "a mesh inner for ventilation"), 30, 2),
    Category("SLB", "Sleeping Bag", "Sleeping bags", (1_999, 14_999),
             ("a 650-fill down blend", "a comfort rating of -5 °C", "a full-length zip",
              "a compression sack", "a draft collar", "a water-resistant shell"), 30, 2),
    Category("BPK", "Backpack", "Backpacks", (1_499, 17_999),
             ("an adjustable torso length", "a rain cover", "hip-belt pockets",
              "a hydration sleeve", "load lifters", "a ventilated back panel"), 30, 3),
    Category("JKT", "Jacket", "Jackets", (2_499, 19_999),
             ("a 2.5-layer waterproof membrane", "pit zips", "a helmet-compatible hood",
              "taped seams", "a packable design", "recycled insulation"), 15, 1),
    Category("BOT", "Hiking Boots", "Hiking boots", (3_499, 16_999),
             ("a Vibram outsole", "a waterproof lining", "a padded ankle collar",
              "a rubber toe cap", "a cushioned midsole", "quick-lace hooks"), 15, 1),
    Category("STV", "Camping Stove", "Camping stoves", (999, 7_999),
             ("a piezo igniter", "a wind shield", "a simmer control",
              "a 2,800 W burner", "foldable pot supports", "a carry case"), 10, 1),
    Category("LMP", "Headlamp", "Headlamps", (499, 4_999),
             ("a 400-lumen beam", "a red night mode", "USB-C charging",
              "an IPX6 rating", "a tilting head", "a lock mode"), 10, 1),
    Category("TRK", "Trekking Poles", "Trekking poles", (999, 6_999),
             ("carbon shafts", "cork grips", "flick locks",
              "tungsten tips", "snow baskets", "a foldable design"), 15, 2),
)

SERIES = ("Alpine", "Summit", "Ridge", "Trail", "Glacier", "Canyon", "Monsoon", "Himalaya",
          "Sahyadri", "Nilgiri", "Zanskar", "Spiti", "Kaveri", "Aravalli", "Coorg", "Ladakh")
VARIANTS = ("", " 2", " 3", " Lite", " Pro", " XL", " Ultra")
USES = ("weekend treks in the Sahyadris", "high-altitude expeditions", "monsoon camping",
        "family car camping", "fast-and-light hiking", "winter treks above 4,000 m")
STATUSES = ("in_stock", "low_stock", "out_of_stock", "discontinued")
STATUS_WEIGHTS = (70, 15, 10, 5)

DEMO_USERS = (
    ("admin@northwind.test", "Asha Admin", ["admins", "managers"]),
    ("agent@northwind.test", "Ravi Support", ["support"]),
    ("manager@northwind.test", "Neha Manager", ["managers"]),
    ("customer@example.com", "Kiran Customer", []),
)
FIRST_NAMES = ("Aarav", "Diya", "Ishaan", "Meera", "Kabir", "Ananya",
               "Rohan", "Priya", "Vikram", "Sana", "Arjun", "Tara")
LAST_NAMES = ("Sharma", "Iyer", "Patel", "Reddy", "Khan", "Das", "Nair", "Singh", "Gupta", "Joshi")
EXTRA_USER_GROUPS = ([["support"]] * 3 + [["sales"]] * 3 + [["sales", "managers"]]
                     + [["hr"]] * 2 + [[]] * 7)

TERRAINS = ("monsoon treks", "high-altitude snow", "desert camping", "rainforest trails",
            "beach camping", "winter car camping", "rocky ridgelines", "river valleys")
DESTINATIONS = ("Ladakh", "Spiti Valley", "Sikkim", "Munnar", "Coorg", "Kedarkantha",
                "Hampta Pass", "Valley of Flowers", "Rishikesh", "Kodaikanal", "Meghalaya", "Kasol")
SEASONS = ("summer", "monsoon", "winter")

ISSUES = ("Late delivery", "Wrong item shipped", "Missing parts", "Damaged packaging",
          "Payment failed but money debited", "Courier marked delivered but not received",
          "Size exchange", "Bulk order query")
SUPPLIERS = tuple(f"{a} {b}" for a in ("Sahyadri", "Konkan", "Deccan", "Malabar", "Doon")
                  for b in ("Textiles", "Outdoor Works", "Polymers", "Metalcraft"))
SEGMENTS = ("schools", "colleges", "trekking clubs", "corporate offsites",
            "adventure tour operators", "NCC units", "mountaineering institutes", "resorts",
            "film production crews", "government departments")
HR_TOPICS = ("Leave policy", "Travel reimbursement", "Night shift allowance",
             "Warehouse safety training", "Probation reviews", "Referral bonus", "Remote work",
             "Festival holidays", "Parental leave", "Performance appraisal cycle",
             "Code of conduct", "Exit process")
REGIONS = ("North", "South", "East", "West", "Central", "North-East")
PROCEDURES = ("Opening the store", "Closing the store", "Handling walk-in returns",
              "Gift card issues", "Price match requests", "Loyalty points corrections",
              "Fitting room checks", "Demo tent setup")
INCIDENTS = ("Customer injury in store", "Product safety complaint", "Data privacy request",
             "Fraudulent order", "Courier damage claim", "Social media complaint",
             "Warehouse stock mismatch", "Recall notice")


def stable_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(ID_NAMESPACE, f"{kind}:{key}")


def body(sections: dict[str, str]) -> str:
    # "## Heading" sections, so the chunker can later split articles by heading.
    return "\n\n".join(f"## {heading}\n\n{text}" for heading, text in sections.items())


def build_users(rng: random.Random) -> list[User]:
    users = [User(stable_id("user", email), email, name, groups)
             for email, name, groups in DEMO_USERS]
    names = rng.sample([(f, l) for f in FIRST_NAMES for l in LAST_NAMES], len(EXTRA_USER_GROUPS))
    for (first, last), groups in zip(names, EXTRA_USER_GROUPS):
        domain = "northwind.test" if groups else "example.com"
        email = f"{first}.{last}@{domain}".lower()
        users.append(User(stable_id("user", email), email, f"{first} {last}", groups))
    return users


def build_products(rng: random.Random) -> list[Product]:
    # The demo's hero record: "How much is the Alpine 2 tent?"
    products = [Product(
        stable_id("product", "TNT-0001"), "TNT-0001", "Alpine 2 Tent",
        "The Alpine 2 Tent is our best-selling two-person tent for weekend treks in the "
        "Sahyadris. It features aluminium poles, a 3,000 mm waterproof fly and two vestibules.",
        12_999, "in_stock",
    )]
    combos = [(c, s, v) for c in CATEGORIES for s in SERIES for v in VARIANTS
              if (c.code, s, v) != ("TNT", "Alpine", " 2")]
    counters = {c.code: 0 for c in CATEGORIES}
    counters["TNT"] = 1
    for cat, series, variant in rng.sample(combos, PRODUCT_COUNT - 1):
        counters[cat.code] += 1
        sku = f"{cat.code}-{counters[cat.code]:04d}"
        name = f"{series}{variant} {cat.noun}"
        f1, f2, f3 = rng.sample(cat.features, 3)
        low, high = cat.price_range
        price = rng.randrange(low // 500 + 1, high // 500 + 1) * 500 - 1  # ends in 499 / 999
        products.append(Product(
            stable_id("product", sku), sku, name,
            f"The {name} is built for {rng.choice(USES)}. It features {f1}, {f2} and {f3}.",
            price, rng.choices(STATUSES, STATUS_WEIGHTS)[0],
        ))
    return products


def core_articles(rng: random.Random) -> list[Document]:
    windows = "\n".join(f"- {c.plural}: {c.return_days} days from delivery" for c in CATEGORIES)
    warranty = "\n".join(
        f"- {c.plural}: {c.warranty_years} year{'s' if c.warranty_years > 1 else ''}"
        for c in CATEGORIES
    )
    pages = {
        "Returns and refunds": {
            "Return windows": f"You can return unused items within these windows:\n\n{windows}",
            "Condition of returned items": "Items must be unused, clean and in their original "
            "packaging with all tags attached. Used footwear and opened stove fuel cannot be returned.",
            "Refund timeline": "Once the return reaches our warehouse, refunds reach the original "
            "payment method within 5-7 business days.",
        },
        "Shipping times and charges": {
            "Delivery times": "Metro cities: 2-3 business days. Rest of India: 4-7 business days. "
            "Ladakh, Andaman and the North-East: up to 10 business days.",
            "Charges": "Shipping is free on orders above ₹2,999. Smaller orders pay a flat ₹149.",
            "Dispatch": "Orders placed before 2 pm IST on a business day are dispatched the same day.",
        },
        "Warranty coverage": {
            "Warranty periods": "Every Northwind Gear product is covered against manufacturing "
            f"defects for:\n\n{warranty}",
            "What is not covered": "Normal wear and tear, damage from misuse, unauthorised repairs "
            "and lost parts are not covered.",
            "How to claim": "Contact support with your order number and photos of the defect. "
            "We repair, replace or refund at our discretion.",
        },
        "Payment methods": {
            "Accepted methods": "We accept UPI, credit and debit cards, net banking and "
            "Northwind Gear gift cards.",
            "Cash on delivery": "Cash on delivery is available for orders up to ₹10,000, "
            "with a ₹49 handling fee.",
        },
        "Tracking your order": {
            "Tracking link": "You receive a tracking link by SMS and email as soon as your order "
            "is dispatched.",
            "Delays": "If tracking has not updated for 3 business days, contact support and we "
            "will chase the courier.",
        },
        "Cancelling an order": {
            "Before dispatch": "Orders can be cancelled free of charge from My Orders until they "
            "are dispatched.",
            "After dispatch": "Once dispatched, refuse the delivery or start a return after it arrives.",
        },
    }
    return [Document(stable_id("document", title), "article", title, body(sections), True)
            for title, sections in pages.items()]


def guide_articles(rng: random.Random, count: int) -> list[Document]:
    pool: list[tuple[str, dict[str, str]]] = []
    for cat in CATEGORIES:
        noun = cat.noun.lower()
        for series in SERIES:
            pool.append((f"Caring for your {series} {noun}", {
                "Cleaning": f"Clean the {series} {noun} with lukewarm water below "
                f"{rng.choice([30, 35, 40])} °C and a mild soap. Never use bleach or a washing machine.",
                "Drying": f"Dry it fully in the shade for at least {rng.randint(6, 24)} hours "
                "before packing it away.",
                "Storage": "Store it loose in a cool, dry place. Re-proof or service it every "
                f"{rng.randint(5, 20)} trips.",
            }))
            pool.append((f"Troubleshooting the {series} {noun} range", {
                "Common issues": f"The most common issue reported for the {series} range is "
                f"wear on {rng.choice(cat.features)} after heavy use.",
                "Quick fixes": "Most problems are solved with the repair kit included in the box. "
                f"Spare parts ship within {rng.randint(2, 7)} business days.",
                "When to contact support": f"If the product fails within its {cat.warranty_years}-year "
                "warranty, contact support for a free repair.",
            }))
    for cat in CATEGORIES:
        for terrain in TERRAINS:
            f1, f2 = rng.sample(cat.features, 2)
            pool.append((f"Choosing a {cat.noun.lower()} for {terrain}", {
                "What matters most": f"For {terrain}, look for {f1} and {f2}.",
                "Our picks": f"The {rng.choice(SERIES)} and {rng.choice(SERIES)} ranges are the "
                f"most popular choices for {terrain}.",
            }))
    for dest in DESTINATIONS:
        for season in SEASONS:
            picks = ", ".join(c.plural.lower() for c in rng.sample(CATEGORIES, 4))
            pool.append((f"Packing list: {dest} in {season}", {
                "Essentials": f"For {dest} in {season}, pack {picks}.",
                "Weather": f"Expect night temperatures around {rng.randint(-15, 18)} °C and plan "
                "for sudden weather changes.",
            }))
    return [Document(stable_id("document", title), "article", title, body(sections), True)
            for title, sections in rng.sample(pool, count)]


def build_policies(rng: random.Random) -> list[Document]:
    specs: list[tuple[str, list[str], dict[str, str]]] = []
    for cat in CATEGORIES:
        specs.append((f"Discount rules for damaged {cat.plural.lower()}", ["support", "managers"], {
            "Allowed discount": f"Support agents may offer up to {rng.choice([10, 15, 20])}% off on "
            f"{cat.plural.lower()} damaged in transit, once the customer sends a photo of the damage.",
            "Escalation": "Larger discounts, or any discount on orders above "
            f"₹{rng.choice([15, 20, 25]) * 1000:,}, need a manager's approval.",
        }))
    for issue in ISSUES:
        specs.append((f"Escalation guide: {issue.lower()}", ["support"], {
            "First response": f"Acknowledge the customer within {rng.choice([2, 4, 6])} hours and "
            "log the case in the helpdesk.",
            "Escalate when": "Escalate to a team lead if the case is unresolved after "
            f"{rng.randint(2, 5)} business days.",
        }))
    for supplier in SUPPLIERS:
        specs.append((f"Supplier terms: {supplier}", ["managers"], {
            "Payment terms": f"Net {rng.choice([30, 45, 60])} days from invoice; "
            f"{rng.choice([1, 2])}% early-payment discount within 10 days.",
            "Order terms": f"Minimum order of {rng.choice([100, 200, 500])} units; lead time "
            f"{rng.randint(3, 10)} weeks.",
            "Margin": f"Target gross margin on this supplier's products: {rng.randint(28, 45)}%.",
        }))
    for segment in SEGMENTS:
        specs.append((f"Bulk pricing for {segment}", ["sales", "managers"], {
            "Discount tiers": f"{rng.choice([5, 8, 10])}% off above 20 units and "
            f"{rng.choice([12, 15])}% off above 100 units for {segment}.",
            "Approval": "Quotes above ₹5,00,000 need a manager's sign-off.",
        }))
    for topic in HR_TOPICS:
        specs.append((f"HR note: {topic.lower()}", ["hr"], {
            "Policy": f"This note sets out Northwind Gear's rules on {topic.lower()}. It was last "
            f"reviewed in {rng.choice(['January', 'April', 'July', 'October'])}.",
            "Contact": "Questions go to the HR team through the internal helpdesk.",
        }))
    for region in REGIONS:
        specs.append((f"Stock allocation: {region} region", ["managers", "sales"], {
            "Allocation": f"The {region} region receives {rng.randint(8, 25)}% of new seasonal stock.",
            "Rebalancing": f"Stock is rebalanced between warehouses every {rng.choice([2, 4, 6])} weeks.",
        }))
    for procedure in PROCEDURES:
        specs.append((f"Store procedure: {procedure.lower()}", ["support", "sales"], {
            "Steps": f"Follow the {rng.randint(4, 9)}-step checklist in the store binder for "
            f"{procedure.lower()}.",
            "Sign-off": "The shift lead signs off the checklist at the end of each shift.",
        }))
    for incident in INCIDENTS:
        specs.append((f"Incident handling: {incident.lower()}", ["support", "managers"], {
            "Immediate steps": f"Record the incident within {rng.choice([1, 2, 4])} hours using the "
            "incident form.",
            "Follow-up": "A manager reviews every incident report within one business day.",
        }))
    return [Document(stable_id("document", title), "policy", title, body(sections), False, groups)
            for title, groups, sections in specs]


def build() -> Catalog:
    rng = random.Random(SEED)
    users = build_users(rng)
    products = build_products(rng)
    core = core_articles(rng)
    articles = core + guide_articles(rng, ARTICLE_COUNT - len(core))
    policies = build_policies(rng)
    return Catalog(users, products, articles, policies)