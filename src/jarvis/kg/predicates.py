# Trust ranks, in one place so they can change without a migration.
TRUST = {"user_stated": 100, "contacts": 80, "calendar": 60, "note": 50, "email": 30, "whatsapp": 30}
SINGLE = frozenset({"employer", "city", "birthday", "preferred_channel", "home", "office"})  # one active value per subject
MULTI = frozenset({"relation_to_user", "friend_of", "note"})
PREDICATES = SINGLE | MULTI
KINDS = ("self", "person", "place", "org", "thing")
