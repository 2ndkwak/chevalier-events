"""
Tests a specific hypothesis: the "+ Add partner" button only gets used
(and only produces the "Guest of" bug) when the partner has no portal
login of their own -- no email/password, so they can't self-RSVP, and the
member can't be added correctly except through the admin tool. Couples
where BOTH partners have real logins can each RSVP for themselves,
sidestepping the button (and its bug) entirely.

Checks this against:
  - The 11 original "Guest of" instances (hardcoded below, from the
    Report 1 output captured before they were fixed)
  - The correctly-paired couples this session's check_correct_pattern.py
    found (also hardcoded, from its own output)

Read-only. Prints a simple table, draws no changes.
"""
from backend import create_app
from backend.models import Person


GUEST_OF_PAIRS = [
    ("Tom Warren", "Jenna Warren"),
    ("Bob Conrad", "Laural Conrad"),
    ("Bruce Mavec", "Ellen Mavec"),
    ("Timothy Reynolds", "Mary Reynolds"),
    ("Nick Ogan", "Anne Ogan"),
    ("Reg Shiverick", "Lynn Shiverick"),
    ("Ian Grove", "Anna Grove"),
    ("Chris Kramer", "Richard Kramer"),
    ("David Lamb", "Heidi Duncken Alten"),
    ("Jeff Glazer", "Norma Glazer"),
]

CORRECTLY_PAIRED = [
    ("Timothy Reynolds", "Mary Reynolds"),
    ("Kristie Beck Burger", "Todd Burger"),
    ("Bob Conrad", "Laural Conrad"),
    ("Ruth Eppig", "Michael Eppig"),
    ("Kim Hartwell", "Sam Hartwell"),
    ("Iris Harvie", "Tom Harvie"),
    ("Judy Kushner", "Philip Kushner"),
    ("Nancy Mino", "John Mino"),
    ("Bobbi Pincus", "Bob Pincus"),
    ("Marilee Strang", "David Strang"),
    ("Michael Kennedy", "Terri Kennedy"),
    ("Marilyn Eisele", "Mark Eisele"),
    ("Michelle Jeschelnig", "Rich Jeschelnig"),
    ("Jennifer Ogan", "Alex Ogan"),
    ("Terri Parker", "John Parker"),
    ("Celia Sinclair", "Jeff Sinclair"),
    ("Virginia M. Taylor", "Bruce Taylor"),
    ("Diane Wynshaw-Boris", "Tony Wynshaw-Boris"),
    ("Hyun Park", "Catherine Park"),
    ("David Bauders", "Dolores Bauders"),
    ("Scott Ryan", "Lauren Ryan"),
]


def find_person(full_name):
    first, *rest = full_name.split(" ")
    last = " ".join(rest)
    return Person.query.filter_by(first_name=first, last_name=last).first()


def has_login(person):
    return bool(person and person.email and person.password_hash)


def main():
    app = create_app()
    with app.app_context():
        print("=" * 78)
        print("GUEST OF pairs -- does the guest-recorded partner have a login?")
        print("=" * 78)
        both_login = 0
        for a_name, b_name in GUEST_OF_PAIRS:
            a, b = find_person(a_name), find_person(b_name)
            a_login, b_login = has_login(a), has_login(b)
            if a_login and b_login:
                both_login += 1
            print(f"  {a_name} (login: {a_login})  <->  {b_name} (login: {b_login})")
        print(f"\n{both_login} of {len(GUEST_OF_PAIRS)} had BOTH partners with their own login "
              f"(hypothesis predicts this should be low/zero).")

        print()
        print("=" * 78)
        print("CORRECTLY PAIRED couples -- do both have logins?")
        print("=" * 78)
        both_login = 0
        for a_name, b_name in CORRECTLY_PAIRED:
            a, b = find_person(a_name), find_person(b_name)
            a_login, b_login = has_login(a), has_login(b)
            if a_login and b_login:
                both_login += 1
            print(f"  {a_name} (login: {a_login})  <->  {b_name} (login: {b_login})")
        print(f"\n{both_login} of {len(CORRECTLY_PAIRED)} had BOTH partners with their own login "
              f"(hypothesis predicts this should be high).")


if __name__ == "__main__":
    main()
