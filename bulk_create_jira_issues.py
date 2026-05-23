#!/usr/bin/env python3
"""Bulk create Jira issues from a CSV file.

Usage:
  python bulk_create_jira_issues.py --file sample_tickets.csv --dry-run

Options:
  --file FILE      CSV file path (default: sample_tickets.csv)
  --dry-run        Print actions without creating issues
  --limit N        Limit number of issues to process

CSV columns expected: project,summary,description,issuetype,labels,priority,assignee,linked_issue
"""
import csv
import argparse
import sys
from pathlib import Path

def load_rows(path):
    with open(path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        return [r for r in reader]

def get_config_from_create():
    # reuse get_config from create_jira_issue if available
    try:
        from create_jira_issue import get_config
        return get_config()
    except Exception:
        return {}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--file', default='sample_tickets.csv')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--limit', type=int, default=0)
    args = p.parse_args()

    path = Path(args.file)
    if not path.exists():
        print('CSV file not found:', path)
        sys.exit(2)

    rows = load_rows(path)
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    cfg = get_config_from_create()
    server = cfg.get('server')
    user = cfg.get('user')
    token = cfg.get('token')

    if args.dry_run:
        print(f'DRY RUN: {len(rows)} issues would be processed from {path}\n')
        for i, r in enumerate(rows, start=1):
            print(f'{i}. [{r.get("project")}] {r.get("summary")} ({r.get("issuetype")}) labels={r.get("labels")}')
        sys.exit(0)

    if not server or not user or not token:
        print('Missing Jira config; set env or .env (JIRA_SERVER, ATLASSIAN_USER_EMAIL, ATLASSIAN_API_TOKEN)')
        sys.exit(2)

    try:
        from jira import JIRA
    except Exception as e:
        print('Failed to import jira client:', e)
        sys.exit(3)

    try:
        jira = JIRA(server=server, basic_auth=(user, token))
    except Exception as e:
        print('Failed to connect to Jira:', e)
        sys.exit(4)

    created = []
    for r in rows:
        fields = {
            'project': {'key': r.get('project') or cfg.get('project') or 'REQ'},
            'summary': r.get('summary') or 'No summary',
            'description': r.get('description') or '',
            'issuetype': {'name': r.get('issuetype') or 'Task'},
        }
        labels = r.get('labels')
        if labels:
            fields['labels'] = [x.strip() for x in labels.split(',') if x.strip()]
        try:
            issue = jira.create_issue(fields=fields)
            print('Created', issue.key, '-', fields['summary'])
            created.append(issue.key)
            # add comment to mark as synthetic/hackathon sample
            jira.add_comment(issue, 'Sample ticket created by bulk_create_jira_issues.py for hackathon demo.')
            # link if requested
            if r.get('linked_issue'):
                try:
                    jira.create_issue_link('Relates', inwardIssue=issue.key, outwardIssue=r.get('linked_issue'))
                except Exception:
                    pass
        except Exception as e:
            print('Failed to create:', fields['summary'], '=>', e)

    print('\nDone. Created', len(created), 'issues.')

if __name__ == '__main__':
    main()
