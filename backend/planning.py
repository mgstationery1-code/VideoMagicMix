from __future__ import annotations


def normalize_output_limit(value: str | int | None) -> int | None:
    """Normalize the quantity field; blank and zero mean unlimited."""
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError("生成数量必须是大于或等于 0 的整数")
    number = int(text)
    return None if number == 0 else number


def allocate_round_robin(total_limit: int | None, estimates: list[int]) -> list[int]:
    """Allocate a global output budget fairly across ready schemes."""
    if total_limit is None:
        return [max(0, estimate) for estimate in estimates]
    allocations = [0 for _ in estimates]
    remaining = total_limit
    while remaining > 0:
        progressed = False
        for index, estimate in enumerate(estimates):
            if allocations[index] >= max(0, estimate):
                continue
            allocations[index] += 1
            remaining -= 1
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            break
    return allocations
