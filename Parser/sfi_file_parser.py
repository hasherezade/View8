from Parser.shared_function_info import SharedFunctionInfo, CodeLine
from parse import parse
import re
import json

all_functions = {}
repeat_last_line = False
known_constant_values = {}


def set_repeat_line_flag(flag):
    global repeat_last_line
    repeat_last_line = flag


def get_next_line(file):
    with open(file, encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield line
            if repeat_last_line:
                set_repeat_line_flag(False)
                yield line
    yield None


def parse_array(lines, func_name):
    if "Start " not in (line := next(lines)):
        raise Exception(f"Error got line \"{line}\" not Start Array")
    const_list = parse_const_array(lines, func_name)
    array_literal = "[" + ", ".join(const_list) + "]"
    while "End " not in (line := next(lines)):
        pass
    if (line := next(lines)) != ">":
        set_repeat_line_flag(True)
    return array_literal


def parse_object(lines, func_name):
    if "Start " not in (line := next(lines)):
        raise Exception(f"Error got line \"{line}\" not Start Object")
    const_list = iter(parse_const_array(lines, func_name)[1:])
    object_literal = "{" + ", ".join([f"{key}: {value}" for key, value in zip(const_list, const_list)]) + "}"
    while "End " not in (line := next(lines)):
        pass
    return object_literal


def parse_bytecode_line(line):
    match = re.search(r"^[^@]+@ +(\d+) : ((?:[0-9a-fA-F]{2} )+) *(.+)$", line)
    if match:
        offset, opcode, inst = match.groups()
        return CodeLine(opcode=opcode, line=int(offset), inst=inst)
    raise ValueError(f"Invalid bytecode line format: {line}")


def parse_bytecode(line, lines):
    code_list = []
    while " @ " in line:
        code_list.append(parse_bytecode_line(line))
        line = next(lines)
    set_repeat_line_flag(True)
    return code_list



def _decode_printed_string(value):
    """Convert V8's <String[n]: #...> representation to a quoted literal."""
    raw = value.split("#", 1)[-1]
    if raw.endswith(">"):
        raw = raw[:-1]
    return json.dumps(raw)


def collect_known_constant_values(file):
    """Pre-scan a disassembly for safely printed constant values.

    The V8 13 analysis printer intentionally leaves many cache-local constants
    opaque as RawConst_0x.... The same heap object is often used in another
    function as an LdaConstant and therefore printed safely as a String.  Heap
    addresses are stable for the lifetime of one disassembly run, so those
    clear occurrences can be used to resolve opaque occurrences elsewhere.
    """
    known_constant_values.clear()
    conflicts = 0
    line_re = re.compile(
        r"^\d+(?:\-\d+)?:\s+(0x[0-9a-fA-F]+)\s+(.+)$"
    )

    with open(file, encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.strip()
            match = line_re.match(line)
            if not match:
                continue

            address, value = match.groups()
            if not value.startswith("<String"):
                continue

            address = address.lower()
            decoded = _decode_printed_string(value)
            previous = known_constant_values.get(address)
            if previous is not None and previous != decoded:
                conflicts += 1
                continue
            known_constant_values[address] = decoded

    print(
        f"Recovered {len(known_constant_values)} cache-local string identities "
        f"from disassembly address reuse."
    )
    if conflicts:
        print(f"[!] Ignored {conflicts} conflicting constant-address mapping(s).")


def resolve_opaque_constant(value):
    match = re.fullmatch(r"(?:RawConst|ROConst)_(0x[0-9a-fA-F]+)", value)
    if not match:
        return None
    return known_constant_values.get(match.group(1).lower())

def parse_const_line(lines, func_name):
    var_line = next(lines)
    match = re.search(r"^(\d+(?:\-\d+)?):\s(0x[0-9a-fA-F]+\s)?(.+)", var_line)
    if not match:
        raise ValueError(f"Invalid constant line format: {var_line}")

    idx_range, address, value = match.groups()
    var_idx = int(idx_range.split('-')[-1]) + 1

    resolved = resolve_opaque_constant(value)
    if resolved is not None:
        return var_idx, resolved

    if not address:
        return var_idx, value
    if value.startswith("<String"):
        return var_idx, _decode_printed_string(value)
    if value.startswith("<SharedFunctionInfo"):
        value = value.split(" ", 1)[-1].rstrip('> ') if " " in value else ""
        return var_idx, parse_shared_function_info(lines, value, func_name)
    if value.startswith("<ArrayBoilerplateDescription") or value.startswith("<FixedArray"):
        return var_idx, parse_array(lines, func_name)
    if value.startswith("<ObjectBoilerplateDescription"):
        return var_idx, parse_object(lines, func_name)
    if value.startswith("<Odd Oddball"):
        return var_idx, "null"
    return var_idx, value.rstrip('>').split(" ", 1)[-1]


def parse_const_array(lines, func_name):
    while "- length:" not in (line := next(lines)):
        pass
    size = int(parse("- length:{}", line)[0])
    if not size:
        return []

    while not (line := next(lines)).startswith("0"):
        pass
    set_repeat_line_flag(True)

    value = ""
    next_idx = 0
    const_list = []

    for idx in range(size):
        if next_idx != idx:
            const_list.append(value)
            continue
        next_idx, value = parse_const_line(lines, func_name)
        const_list.append(value)

    return const_list


def parse_const_pool(line, lines, func_name):
    if "size = 0" in line:
        return []

    size_match = re.search(r"size\s*=\s*(\d+)", line)
    if not size_match:
        raise ValueError(f"Invalid constant-pool header: {line}")
    size = int(size_match.group(1))

    # Analysis-only V8 13.x output used when the code cache was built against
    # a different read-only snapshot. It deliberately avoids dereferencing
    # constant-pool HeapObjects and prints one raw tagged value per slot.
    first = next(lines)
    if re.match(r"^\[\d+\]\s+raw=0x[0-9a-fA-F]+", first):
        values = []
        current = first
        for expected_idx in range(size):
            match = re.match(
                r"^\[(\d+)\]\s+raw=(0x[0-9a-fA-F]+)(?:\s+Smi=(-?\d+)|\s+HeapObject)?$",
                current,
            )
            if not match:
                raise ValueError(f"Invalid raw constant-pool line: {current}")
            idx, raw, smi = match.groups()
            if int(idx) != expected_idx:
                raise ValueError(
                    f"Unexpected raw constant-pool index {idx}, expected {expected_idx}"
                )
            values.append(smi if smi is not None else f"RawConst_{raw}")
            if expected_idx + 1 < size:
                current = next(lines)
        return values

    # Legacy recursive FixedArray printer. Put the first consumed line back.
    set_repeat_line_flag(True)
    return parse_const_array(lines, func_name)


def parse_exception_table_line(line):
    from_, to_, key, _ = parse("({},{})  -> {} ({}", line)
    return int(key), [int(from_), int(to_)]


def parse_handler_table(line, lines):
    if "size = 0" in line:
        return {}
    exception_table = {}
    next(lines)
    while " -> " in (line := next(lines)):
        key, value = parse_exception_table_line(line)
        exception_table[key] = value
    set_repeat_line_flag(True)
    return exception_table


def parse_parameter_count(line):
    return int(parse("Parameter count {}", line)[0])


def parse_register_count(line):
    return int(parse("Register count {}", line)[0])


def parse_address(line):
    # V8 <= 10.x printed e.g. "0x...: [SharedFunctionInfo] in OldSpace".
    # Newer V8 versions omit the trailing "in ...". Accept both.
    match = re.match(r"^(0x[0-9a-fA-F]+):\s+\[[^\]]+\](?:\s+in\s+.*)?$", line)
    if not match:
        raise ValueError(f"Invalid object address line: {line}")
    return match.group(1)


def parse_shared_function_info(lines, name, declarer=None):
    sfi = SharedFunctionInfo()
    sfi.declarer = declarer
    sfi.name = 'func_unknown'
    address = ""
    while (line := next(lines)) not in ("End SharedFunctionInfo", None):
        if line.startswith("- name_ref:"):
            ref = line.split(":", 1)[1].strip()
            if re.fullmatch(r"(?:ROConst|RawConst)_0x[0-9a-fA-F]+", ref):
                sfi.name_ref = ref
        elif "Parameter count" in line:
            sfi.argument_count = parse_parameter_count(line)
        elif "Register count" in line:
            sfi.register_count = parse_register_count(line)
        elif "Constant pool" in line:
            sfi.const_pool = parse_const_pool(line, lines, sfi.name)
        elif "Handler Table" in line:
            sfi.exception_table = parse_handler_table(line, lines)
        elif "@    0 : " in line:
            sfi.code = parse_bytecode(line, lines)
        elif "[SharedFunctionInfo]" in line or "[BytecodeArray]" in line:
            address = parse_address(line)
            sfi.name = f'func_{(name or "unknown")}_{address}'

    all_functions[sfi.name] = sfi

    if not sfi.is_fully_parsed():
        raise ValueError(f"Incomplete parsing of function: {sfi.name}")

    return sfi.name


def parse_file(file="test.txt"):
    all_functions.clear()
    collect_known_constant_values(file)

    lines = get_next_line(file)
    while next(lines) != "Start SharedFunctionInfo":
        pass

    parse_shared_function_info(lines, "start")
    return all_functions


if __name__ == '__main__':
    parse_file()
