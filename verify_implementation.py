from flexrouter.recovery import PenaltyBox
import time

# Test exponential backoff formula
pb = PenaltyBox(base_seconds=30, max_seconds=1800)

# First penalize: base * 2^(1-1) = 30 * 2^0 = 30
pb.penalize("groq", "llama")
print(f"1st penalty: {pb.penalty_seconds('groq', 'llama')} (expect 30)")

# Second: base * 2^(2-1) = 30 * 2^1 = 60
pb.penalize("groq", "llama")
print(f"2nd penalty: {pb.penalty_seconds('groq', 'llama')} (expect 60)")

# Third: base * 2^(3-1) = 30 * 2^2 = 120
pb.penalize("groq", "llama")
print(f"3rd penalty: {pb.penalty_seconds('groq', 'llama')} (expect 120)")

# Tenth: base * 2^(10-1) = 30 * 2^9 = 15360, capped at 1800
pb2 = PenaltyBox(base_seconds=30, max_seconds=1800)
for i in range(10):
    pb2.penalize("test", "model")
print(f"10th penalty: {pb2.penalty_seconds('test', 'model')} (expect 1800, capped)")

# Test penalty_until is monotonic and consistent
pb3 = PenaltyBox(base_seconds=10, max_seconds=100)
pb3.penalize("a", "b")
t1 = pb3.penalty_until("a", "b")
time.sleep(0.1)
t2 = pb3.penalty_until("a", "b")
print(f"\nmonotonic until: {t1 >= t2} (expect True - time moves forward, until gets earlier)")

# Test penalty_seconds when not penalized
pb4 = PenaltyBox(base_seconds=30, max_seconds=1800)
print(f"\npenalty_seconds when not penalized: {pb4.penalty_seconds('x', 'y')} (expect 30 for count=0)")

# Test penalize_short doesn't increment count
pb5 = PenaltyBox(base_seconds=30, max_seconds=1800)
pb5.penalize("a", "b")
first = pb5.penalty_seconds("a", "b")
pb5.penalize_short("a", "b", seconds=100)  # Should reset without incrementing count
second = pb5.penalty_seconds("a", "b")
print(f"\nAfter penalize_short: first={first}, second={second} (expect 30, 30 - count preserved)")
