# Bidirectional-ish common nickname groups. Each inner list is a cluster of
# names/nicknames that should all be treated as plausibly the same person.
NICKNAME_GROUPS = [
    ["robert", "bob", "rob", "bobby", "robbie"],
    ["william", "bill", "will", "billy", "liam"],
    ["richard", "rich", "rick", "dick", "ricky"],
    ["james", "jim", "jimmy", "jamie"],
    ["john", "jack", "johnny"],
    ["thomas", "tom", "tommy"],
    ["charles", "charlie", "chuck", "chas"],
    ["christopher", "chris", "topher"],
    ["daniel", "dan", "danny"],
    ["david", "dave", "davy"],
    ["joseph", "joe", "joey"],
    ["matthew", "matt"],
    ["michael", "mike", "mikey", "mick"],
    ["anthony", "tony", "anton"],
    ["steven", "stephen", "steve", "stevie"],
    ["edward", "ed", "eddie", "ted", "teddy"],
    ["frederick", "fred", "freddie", "freddy"],
    ["theodore", "ted", "teddy"],
    ["alexander", "alex", "sandy", "xander", "al"],
    ["nicholas", "nick", "nicky"],
    ["samuel", "sam", "sammy"],
    ["jeffrey", "jeff", "geoff"],
    ["zachary", "zachery", "zach", "zack"],
    ["reginald", "reg", "reggie"],
    ["gregory", "greg", "gregg"],
    ["timothy", "tim", "timmy"],
    ["kenneth", "ken", "kenny"],
    ["benjamin", "ben", "benny"],
    ["jonathan", "jon", "jonny", "johnny"],
    ["andrew", "andy", "drew"],
    ["douglas", "doug"],
    ["patrick", "pat", "paddy"],
    ["lawrence", "larry"],
    ["raymond", "ray"],
    ["gerald", "jerry"],
    ["harold", "harry"],
    ["walter", "walt"],
    ["peter", "pete"],
    ["vincent", "vince", "vinny"],
    ["nathaniel", "nathan", "nate"],
    ["trenton", "trent"],
    ["claiborne", "clay"],
    ["elizabeth", "beth", "liz", "betty", "eliza", "lisa", "libby"],
    ["katherine", "catherine", "kathy", "kate", "katie", "cathy", "kathleen", "kay"],
    ["margaret", "meg", "maggie", "peggy", "marge"],
    ["susan", "sue", "susie", "suzy"],
    ["deborah", "deb", "debbie"],
    ["barbara", "barb", "babs"],
    ["patricia", "pat", "patty", "trish"],
    ["cynthia", "cindy"],
    ["jennifer", "jen", "jenny", "jenn"],
    ["christine", "christina", "chris", "tina", "christy"],
    ["rebecca", "becky", "reba"],
    ["victoria", "vicky", "tori", "vicki"],
    ["sandra", "sandy"],
    ["jacqueline", "jackie"],
    ["alexandra", "alex", "sandy", "lexi", "lexie"],
    ["gabriel", "gabe"],
    ["ronald", "ron", "ronnie"],
    ["donald", "don", "donnie"],
    ["martin", "marty"],
    ["philip", "phil"],
    ["albert", "al"],
    ["arthur", "art"],
    ["eugene", "gene"],
    ["francis", "frank"],
    ["russell", "russ"],
    ["stanley", "stan"],
]

_LOOKUP = {}
for group in NICKNAME_GROUPS:
    gset = set(group)
    for name in group:
        _LOOKUP.setdefault(name, set()).update(gset)


def names_plausibly_match(a, b):
    """True if a and b could reasonably be the same first name -- exact
    match, known nickname pair, prefix match, or close spelling variant."""
    a = (a or "").strip().lower().rstrip(".")
    b = (b or "").strip().lower().rstrip(".")
    if not a or not b:
        return False
    if a == b:
        return True
    if b in _LOOKUP.get(a, ()):
        return True
    if a in _LOOKUP.get(b, ()):
        return True
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    import difflib
    if len(a) >= 4 and len(b) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.82:
        return True
    return False
