#!/usr/bin/env python3

import argparse
import os
from Parser.parse_v8cache import parse_v8cache_file, parse_disassembled_file
from Parser.shared_function_info import save_functions_to_file, load_functions_from_file
from Simplify.global_scope_replace import replace_global_scope



def disassemble(in_file, input_is_disassembled, disassembler):
    out_name = 'disasm.tmp'
    view8_dir = os.path.dirname(os.path.abspath(__file__))
    
    if input_is_disassembled:
        out_name = in_file
    else:
        # Disassemble the file
        parse_v8cache_file(in_file, out_name, view8_dir, disassembler)
    
    return parse_disassembled_file(out_name)


def decompile(all_functions):
    # Decompile
    print(f"Decompiling {len(all_functions)} functions.")
    for name in list(all_functions)[::-1]:
        all_functions[name].decompile()
    # replace_global_scope(all_functions)


def export_to_file(out_name, all_functions, format_list):
    serialize_only = False
    if ('serialized' in format_list):
        serialized_name = out_name
        if len(format_list) == 1:
            serialize_only = True
        else:
            serialized_name += ".pkl"
        print(f"Serializing to file: {serialized_name}")
        save_functions_to_file(all_functions, serialized_name)
        if serialize_only:
            return
    with open(out_name, "w") as f:
        print(f"Exporting to file: {out_name}")
        for function_name in list(all_functions)[::-1]:
            f.write(all_functions[function_name].export(export_v8code="v8_opcode" in format_list, export_translated="translated" in format_list, export_decompiled="decompiled" in format_list))
      

def main():
    parser = argparse.ArgumentParser(description="View8: V8 cache decompiler.")
    parser.add_argument('--inp', '-i', help="The input file name.", default=None, required=True)
    parser.add_argument('--out', '-o', help="The output file name.", default=None, required=True)
    parser.add_argument('--path', '-p', help="Path to disassembler binary.", default=None)
    parser.add_argument('--disassembled', '-d', action='store_true', help="Indicate if the input file is already disassembled.")
    parser.add_argument('--serialized', '-s', action='store_true', help="Indicate if the input file is serialized.")
    parser.add_argument('--export_format', '-e', nargs='+', choices=['v8_opcode', 'translated', 'decompiled', 'serialized'],
                        help="Specify the export format(s). Options are 'v8_opcode', 'translated', and 'decompiled'. Multiple options can be combined.", 
                        default=['decompiled'])

    args = parser.parse_args()
    
    if not os.path.isfile(args.inp):
        raise FileNotFoundError(f"The input file {args.inp} does not exist.")

    if (args.serialized):
        print(f"Reading from serialized, already decompiled input: {args.inp}")
        all_func = load_functions_from_file(args.inp)
    else:
        all_func = disassemble(args.inp, args.disassembled, args.path)
        decompile(all_func)

    export_to_file(args.out, all_func, args.export_format)
    print(f"Done.")


if __name__ == "__main__":
    main()
