#!/usr/bin/env python3
"""Create a sample Jira issue using env vars or .env in repository root.

Usage:
  python create_jira_issue.py --summary "My summary" --project BANK

The script will try to read `JIRA_SERVER`, `ATLASSIAN_USER_EMAIL`, and
`ATLASSIAN_API_TOKEN` from the environment, and will fall back to parsing
.env in the repo root if any value is missing.
"""
import os
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime

def parse_dotenv(path: Path):
    data = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r'([^=]+)=(.*)', line)
        if not m:
            continue
        k = m.group(1).strip()
        v = m.group(2).strip()
        # remove surrounding quotes
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        data[k] = v
    return data

def get_config():
    cfg = {
        'server': os.getenv('JIRA_SERVER') or os.getenv('ATLASSIAN_BASE_URL'),
        'user': os.getenv('ATLASSIAN_USER_EMAIL'),
        'token': os.getenv('ATLASSIAN_API_TOKEN') or os.getenv('CONFLUENCE_TOKEN'),
        'project': os.getenv('JIRA_PROJECT') or os.getenv('JIRA_PROJECT_KEY'),
    }
    if not cfg['server'] or not cfg['user'] or not cfg['token']:
        envfile = Path(__file__).with_name('.env')
        envdata = parse_dotenv(envfile)
        cfg['server'] = cfg['server'] or envdata.get('JIRA_SERVER') or envdata.get('ATLASSIAN_BASE_URL')
        cfg['user'] = cfg['user'] or envdata.get('ATLASSIAN_USER_EMAIL')
        cfg['token'] = cfg['token'] or envdata.get('ATLASSIAN_API_TOKEN') or envdata.get('CONFLUENCE_TOKEN')
        cfg['project'] = cfg['project'] or envdata.get('JIRA_PROJECT') or envdata.get('JIRA_PROJECT_KEY')
    return cfg

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--summary', default=None)
    p.add_argument('--description', default=None)
    p.add_argument('--project', default=None)
    p.add_argument('--issuetype', default='Task')
    p.add_argument('--labels', default='banking,internal-query')
    args = p.parse_args()

    cfg = get_config()
    project = args.project or cfg.get('project') or 'BANK'
    server = cfg.get('server')
    user = cfg.get('user')
    token = cfg.get('token')

    if not server or not user or not token:
        print('Missing Jira configuration. Please set JIRA_SERVER, ATLASSIAN_USER_EMAIL, and ATLASSIAN_API_TOKEN (or add them to .env).')
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

    summary = args.summary or f'Auto: Banking ticket {datetime.utcnow().isoformat()}'
    description = args.description or (
        'Auto-created ticket for testing Jira integration.\n\n'
        'Fields: internal-query template. Remove sensitive data before attaching.'
    )
    labels = [x.strip() for x in (args.labels or '').split(',') if x.strip()]

    fields = {
        'project': {'key': project},
        'summary': summary,
        'description': description,
        'issuetype': {'name': args.issuetype},
    }
    if labels:
        fields['labels'] = labels

    try:
        issue = jira.create_issue(fields=fields)
        print('OK', issue.key)
        # add a brief comment
        jira.add_comment(issue, 'Created by create_jira_issue.py for testing. Please remove if sensitive.')
    except Exception as e:
        print('Failed to create issue:', e)
        try:
            projs = jira.projects()
            if projs:
                print('\nAccessible projects (key: name):')
                for p in projs[:100]:
                    print(f'{p.key}: {p.name}')
            else:
                print('\nNo accessible projects found for this user.')
        except Exception as e2:
            print('Also failed to list projects:', e2)
        sys.exit(5)

if __name__ == '__main__':
    main()
