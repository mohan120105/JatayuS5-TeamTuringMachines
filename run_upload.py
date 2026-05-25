import os
import sys
import json
from connectors_google import DriveConnector


def main():
    folder = os.path.join(os.path.dirname(__file__), "v1_baseline_docs")
    drive_parent = None
    # Priority: CLI arg, then env var
    if len(sys.argv) > 1:
        drive_parent = sys.argv[1]
    else:
        drive_parent = os.getenv("SHARED_DRIVE_ID")

    print("Uploading folder:", folder)
    if drive_parent:
        print("Target Drive/Folder ID:", drive_parent)
    dc = DriveConnector()
    res = dc.upload_folder(folder, drive_parent_id=drive_parent)
    # Write concise result and print short summary
    out_path = os.path.join(os.path.dirname(__file__), "upload_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"items": res.items, "notes": res.notes}, f, indent=2)

    print(f"Uploaded {len(res.items)} items. Saved details to {out_path}")
    for it in res.items[:5]:
        print("-", it.get("name"), it.get("id"))
    if res.notes:
        print("Notes: see upload_result.json for errors")
        # Pretty print the notes (which is a list of dicts/errors)
        print(json.dumps(res.notes, indent=2))

if __name__ == '__main__':
    main()
