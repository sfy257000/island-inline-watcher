"""Verify that Windows executables embed the project's actual multi-size icon."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


REQUIRED_SIZES = {16, 24, 32, 48, 64, 128, 256}


def read_ico(path):
    data = Path(path).read_bytes()
    reserved, kind, count = struct.unpack_from('<HHH', data)
    if reserved != 0 or kind != 1 or count == 0:
        raise ValueError('Invalid Windows icon header')
    images = {}
    for index in range(count):
        width, height, _, _, _, bits, length, offset = struct.unpack_from('<BBBBHHII', data, 6 + index * 16)
        width, height = width or 256, height or 256
        if width != height or bits != 32 or offset < 6 + count * 16 or offset + length > len(data):
            raise ValueError('Invalid icon image entry')
        if width in images:
            raise ValueError('Duplicate icon size')
        images[width] = hashlib.sha256(data[offset:offset + length]).hexdigest()
    if set(images) != REQUIRED_SIZES:
        raise ValueError(f'Expected icon sizes {sorted(REQUIRED_SIZES)}, got {sorted(images)}')
    return images


def verify_executable(path, expected):
    import pefile  # Installed with the Windows PyInstaller build dependency group.

    with pefile.PE(str(path), fast_load=True) as pe:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
        resources = getattr(pe, 'DIRECTORY_ENTRY_RESOURCE', None)
        if resources is None:
            raise ValueError(f'{path} has no Windows resources')
        icons, groups = {}, []
        for resource_type in resources.entries:
            if resource_type.id not in (3, 14):  # RT_ICON, RT_GROUP_ICON
                continue
            for entry in resource_type.directory.entries:
                for language in entry.directory.entries:
                    info = language.data.struct
                    payload = pe.get_data(info.OffsetToData, info.Size)
                    if resource_type.id == 3:
                        icons.setdefault(entry.id, set()).add(hashlib.sha256(payload).hexdigest())
                    else:
                        groups.append((str(entry.name or entry.id), payload))
        for name, payload in groups:
            reserved, kind, count = struct.unpack_from('<HHH', payload)
            if reserved != 0 or kind != 1:
                continue
            matches = set()
            for index in range(count):
                width, height, _, _, _, _, _, icon_id = struct.unpack_from('<BBBBHHIH', payload, 6 + index * 14)
                width, height = width or 256, height or 256
                if width == height and expected.get(width) in icons.get(icon_id, set()):
                    matches.add(width)
            if matches == REQUIRED_SIZES:
                return {'path': str(path), 'group': name, 'matching_sizes': sorted(matches)}
    raise ValueError(f'{path} does not embed the project icon at every required size')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('icon', type=Path)
    parser.add_argument('executables', type=Path, nargs='*')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    expected = read_ico(args.icon)
    result = {'ok': True, 'sizes': sorted(expected),
              'executables': [verify_executable(path, expected) for path in args.executables]}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
