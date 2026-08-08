import json


def main():
    with open('found_mks.json', encoding='utf-8') as f:
        items = json.load(f)

    items.sort(key=lambda x: x['knesset_member_id'], reverse=True)

    with open('found_mks.md', 'w', encoding='utf-8') as md:
        md.write(f"### Found {len(items)} X Accounts for MKs Missing from `mk_social_account` (Ordered by Knesset Member ID DESC)\n\n")
        md.write("| Knesset Member ID | Status | Hebrew Name | English Name | X Handle | Match Source |\n")
        md.write("|---|---|---|---|---|---|\n")
        for item in items:
            status = "**CURRENT**" if item['is_current'] else "Historical"
            name_he = item['full_name_he'] or "—"
            name_en = item['full_name_en'] or "—"
            handle = f"@{item['handle']}"
            md.write(f"| {item['knesset_member_id']} | {status} | {name_he} | {name_en} | `{handle}` | {item['source']} |\n")

if __name__ == '__main__':
    main()
