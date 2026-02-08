#!/usr/bin/env python3
# gitops-replacer
#
# Accepts a value as argument and updates marked values in YAML files
# in one or more GitHub repositories using a marker-based approach.
#
# Author: Simon Lauger <simon@lauger.de>
#
# Notes:
# - Uses marker comments (# gitops-replacer: <name>) to locate values.
# - Replaces the value on the line immediately following the marker.
# - Preserves comments, quotes, and formatting (no YAML parsing/serialization).
# - Uses requests.Session with retry & timeout.
#
import requests
import json
import os
import sys
import re
import base64
import argparse
import urllib.parse
import yaml
from yaml.loader import SafeLoader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_CONFIG = "gitops-replacer.json"

# Pattern to match marker comment: # gitops-replacer: <name>
MARKER_PATTERN = r'#\s*gitops-replacer:\s*(\S+)'


def make_session():
    sess = requests.Session()
    retries = Retry(
        total=5,
        read=5,
        connect=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "PUT", "HEAD"]),
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retries)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


def replace_marked_value(content: str, dep_name: str, new_value: str) -> tuple[str, str | None, bool]:
    """
    Replace value on line after marker with matching name.

    Returns:
        tuple: (new_content, old_value, changed)
    """
    lines = content.split('\n')
    result = []
    replace_next = False
    old_value = None
    changed = False

    for line in lines:
        # Check if this line is a marker for our depName
        match = re.search(MARKER_PATTERN, line)
        if match and match.group(1) == dep_name:
            replace_next = True
            result.append(line)
            continue

        if replace_next:
            replace_next = False
            # Replace value on this line, preserve key and quotes
            # Pattern: key: value or key: "value" or key: 'value'
            # Also handles YAML list items: - key: value
            # Also handles inline comments after the value
            value_pattern = r'^(\s*(?:-\s+)?[\w-]+:\s*)(["\']?)([^"\'#\n]*)(["\']?)(\s*#.*)?$'
            value_match = re.match(value_pattern, line)
            if value_match:
                prefix = value_match.group(1)       # "    version: " or "  - name: "
                quote_open = value_match.group(2)   # " or ' or empty
                old_value = value_match.group(3).rstrip()  # the actual value (stripped)
                quote_close = value_match.group(4)  # " or ' or empty
                suffix = value_match.group(5) or '' # inline comment if any

                if old_value != new_value:
                    line = f'{prefix}{quote_open}{new_value}{quote_close}{suffix}'
                    changed = True

        result.append(line)

    return '\n'.join(result), old_value, changed


def main():
    parser = argparse.ArgumentParser(description='Marker-based value replacer for GitOps repositories.')
    parser.add_argument(
        '--config',
        metavar='<file>',
        type=str,
        help=f'configuration file (defaults to "{DEFAULT_CONFIG}")',
        default=DEFAULT_CONFIG,
        required=False,
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='if set the changes will be applied to the repository, otherwise the script runs in dry-run mode',
        required=False,
    )
    parser.add_argument(
        '--ci',
        action='store_true',
        help='enable the CI mode, which validates the environment variable GITHUB_REF against patterns in the config file',
        required=False,
    )
    parser.add_argument(
        'value',
        metavar='<string>',
        help='value to set at the marked location',
        type=str,
        default=None,
    )
    parser.add_argument('--name',
        metavar='<string>',
        help='author name which is used during the commit of the changes (env: GIT_COMMIT_NAME)',
        type=str,
        default=os.getenv('GIT_COMMIT_NAME', 'Replacer Bot'),
    )
    parser.add_argument('--email',
        metavar='<string>',
        help='email which is used during the commit of the changes (env: GIT_COMMIT_EMAIL)',
        type=str,
        default=os.getenv('GIT_COMMIT_EMAIL', 'replacer-bot@localhost.localdomain'),
    )
    parser.add_argument('--message',
        metavar='<string>',
        help='commit message template (default to "fix: update {} to {}")',
        type=str,
        default='fix: update {} to {}',
    )
    parser.add_argument('--api',
        metavar='<string>',
        help='URL to the GitHub API (default: "https://api.github.com"; env: GITHUB_API_URL)',
        type=str,
        default=os.getenv('GITHUB_API_URL', 'https://api.github.com'),
    )
    parser.add_argument('--verbose',
        action='store_true',
        help='enable verbose logging (prints file contents and desired state)',
        required=False,
    )

    args = parser.parse_args()

    # get variables from environment
    git_ref      = os.getenv('GITHUB_REF', os.getenv('GIT_REF', None))
    github_token = os.getenv('GITHUB_TOKEN', None)

    # validate variables
    if not github_token:
        print("error: GITHUB_TOKEN is not set")
        sys.exit(1)

    if args.ci and not git_ref:
        print("error: GITHUB_REF is not set (required in --ci mode)")
        sys.exit(1)

    # load config file
    if not os.path.exists(args.config):
        print(f"error: config file {args.config} does not exist")
        sys.exit(1)

    with open(args.config, 'r') as f:
        if args.config.endswith('.json'):
            config = json.load(f)
        else:
            config = yaml.load(f, Loader=SafeLoader)

    if 'gitops-replacer' not in config:
        print("info: no gitops-replacer entry found in config, exiting")
        sys.exit(0)

    print(f"info: run replacer with value '{args.value}'")

    # default to exit code 0
    exit_code = 0

    if not args.apply:
        print("info: running in dry-run, no changes will be applied")

    session = make_session()
    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json',
    }
    timeout = 30

    # precheck block (cache responses for later reuse)
    cache = {}
    for repo in config['gitops-replacer']:
        repo_path = repo['repository']
        branch = repo['branch']
        file_path = repo['file']
        dep_name = repo['depName']
        cache_key = f"{repo_path}:{branch}:{file_path}"

        print(f"info: validate if {file_path} from repository {repo_path} in branch {branch} exists")

        # Use GET for reliability; do not decode content here
        url = f"{args.api}/repos/{repo_path}/contents/{file_path}?ref={urllib.parse.quote(branch)}"
        if args.verbose:
            print(url)
        precheck = session.get(url, headers=headers, timeout=timeout)
        if precheck.status_code == 401:
            print("error: 401 unauthorized - maybe your token does not have access to the defined repository")
            exit_code = 1
        elif precheck.status_code == 404:
            print("error: 404 not found - make sure that the file exists in the defined target")
            exit_code = 1
        elif precheck.status_code != 200:
            print(f"error: unknown error with HTTP code {precheck.status_code}")
            exit_code = 1
        else:
            # Cache successful response for later reuse
            cache[cache_key] = precheck.json()

    if exit_code != 0:
        sys.exit(exit_code)

    # replace block
    for repo in config['gitops-replacer']:
        repo_path = repo['repository']
        branch = repo['branch']
        file_path = repo['file']
        dep_name = repo['depName']

        if args.ci:
            if 'when' in repo:
                if not re.match(repo['when'], git_ref or ""):
                    print(f"info: git-ref {git_ref} does not match when pattern ('{repo['when']}')")
                    continue
                else:
                    print(f"info: git-ref {git_ref} matches when pattern ('{repo['when']}')")
            if 'except' in repo:
                if re.match(repo['except'], git_ref or ""):
                    print(f"info: git-ref {git_ref} matches except pattern ('{repo['except']}')")
                    continue
                else:
                    print(f"info: git-ref {git_ref} does not match except pattern ('{repo['except']}')")

        # get file (reuse cached data from precheck if available)
        cache_key = f"{repo_path}:{branch}:{file_path}"
        if cache_key in cache:
            print(f"info: using cached data for {file_path} from repository {repo_path}")
            fetch_json = cache[cache_key]
        else:
            print(f"info: fetch {file_path} from repository {repo_path} in branch {branch}")
            fetch_url = f"{args.api}/repos/{repo_path}/contents/{file_path}?ref={urllib.parse.quote(branch)}"
            fetch = session.get(fetch_url, headers=headers, timeout=timeout)
            if fetch.status_code != 200:
                try:
                    fetch_json = fetch.json()
                    msg = fetch_json.get('message', 'unknown error')
                except Exception:
                    msg = 'unknown error'
                print(f"error: {msg}")
                exit_code = 1
                continue
            fetch_json = fetch.json()

        content_original = base64.b64decode(fetch_json['content']).decode('utf-8')

        if args.verbose:
            print(f"info: original content of {file_path}:")
            print(f"#### BEGIN OF SOURCE FILE {file_path} ####")
            print(content_original)
            print(f"#### END OF SOURCE FILE {file_path} ####")

        # find marker and replace value
        content, old_value, changed = replace_marked_value(content_original, dep_name, args.value)

        if old_value is None:
            print(f"warn: no marker found for depName '{dep_name}' in {file_path}")
            continue

        print(f"info: depName '{dep_name}' - current value: {old_value}")

        if not changed:
            print(f"info: no outstanding changes for depName '{dep_name}' in file {file_path}")
            continue

        print(f"info: depName '{dep_name}' - new value: {args.value}")

        if args.verbose:
            print(f"info: desired content of {file_path}:")
            print(f"#### BEGIN OF DESIRED FILE {file_path} ####")
            print(content)
            print(f"#### END OF DESIRED FILE {file_path} ####")

        if not args.apply:
            continue

        # update file in repository
        print(f"info: update {file_path} from repository {repo_path} in branch {branch}")
        put_url = f"{args.api}/repos/{repo_path}/contents/{file_path}"
        update = session.put(
            put_url,
            headers={**headers, 'Content-Type': 'application/json'},
            data=json.dumps({
                'committer': {
                    'name': args.name,
                    'email': args.email,
                },
                'message': args.message.format(dep_name, args.value),
                'branch': branch,
                'content': base64.b64encode(content.encode('utf-8')).decode(),
                'sha': fetch_json['sha']
            }),
            timeout=timeout
        )

        try:
            update_json = update.json()
            print(json.dumps(update_json, indent=4))
        except Exception:
            print("warn: could not decode update response as JSON")

        if update.status_code not in (200, 201):
            exit_code = 1

    sys.exit(exit_code)

if __name__ == "__main__":
    main()
