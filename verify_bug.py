from flexrouter.recovery import PenaltyBox

pb = PenaltyBox(base_seconds=30, max_seconds=1800)

# When count=0 (not penalized):
# penalty_seconds should return: min(30 * 2^(0-1), 1800) = min(30 * 2^(-1), 1800) = min(15.0, 1800) = 15.0

# But spec says it should return the CURRENT penalty duration
# If there's no penalty, what should it return?

# Let me check what the formula actually does:
count = 0
result = 30 * (2 ** (count - 1))
print(f"30 * (2 ** (0 - 1)) = 30 * 2^(-1) = {result}")

# The formula is: base_seconds * (2 ** (count - 1))
# When count=0: base_seconds * (2 ** -1) = base_seconds / 2

# This seems like a bug - penalty_seconds should probably:
# - Return 0 if not penalized
# - OR return base_seconds only if never penalized but currently has a valid penalty

# Let me test what actually happens:
pb2 = PenaltyBox(base_seconds=30, max_seconds=1800)
pb2.penalize("a", "b")
print(f"After 1 penalize: {pb2.penalty_seconds('a', 'b')}")
print(f"State: {pb2._state}")

pb2.penalize("a", "b")
print(f"After 2 penalize: {pb2.penalty_seconds('a', 'b')}")
print(f"State: {pb2._state}")

# The issue: penalty_seconds computes from count, but never checks is_penalized
# If the penalty has EXPIRED, count is still non-zero but penalty_seconds returns stale value
