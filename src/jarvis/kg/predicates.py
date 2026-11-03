# Trust ranks, in one place so they can change without a migration.
TRUST = {"user_stated": 100, "contacts": 80, "calendar": 60, "note": 50, "email": 30, "whatsapp": 30}
SINGLE = frozenset({"employer", "city", "birthday", "preferred_channel", "home", "office", "occupation"})  # one active value per subject
MULTI = frozenset({"relation_to_user", "friend_of", "note"})
PREDICATES = SINGLE | MULTI
KINDS = ("self", "person", "place", "org", "thing")

# Links between two entities: a fact whose `object_entity_id` points at the other one. (forward label, label seen from
# the other end); a link is stored once and shown both ways. `friend_of` is also a plain text predicate (above): the
# text form stays valid, and the startup conversion (kg/migrate.py) links it where the name matches one entity.
LINKS = {"spouse_of": ("spouse of", "spouse of"), "partner_of": ("partner of", "partner of"),
         "sibling_of": ("sibling of", "sibling of"), "friend_of": ("friend of", "friend of"),
         "colleague_of": ("colleague of", "colleague of"), "relative_of": ("relative of", "relative of"),
         "parent_of": ("parent of", "child of"), "manager_of": ("manager of", "reports to")}
ALIASES = {"child_of": "parent_of", "reports_to": "manager_of"}  # written by swapping subject and object
SYMMETRIC = frozenset(k for k, (fwd, back) in LINKS.items() if fwd == back)
LINK_TYPES = tuple(LINKS) + tuple(ALIASES)  # what a tool, the extractor or the app may name


def label_for(link_type: str) -> str:
    """How a link type reads from the subject's side: "spouse of", "child of" (for the swapped aliases)."""
    return LINKS[ALIASES[link_type]][1] if link_type in ALIASES else LINKS[link_type][0]
