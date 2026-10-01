#!/usr/bin/env bash
# Applies port/github.json to the repository: the merge settings, then each
# ruleset. A ruleset is found by name and updated in place, or created when no
# ruleset has that name, so running it again changes nothing.
#
#   GH_REPO=owner/repo github-settings.sh             applies the settings and rulesets
#   DRY_RUN=1 GH_REPO=owner/repo github-settings.sh   prints each write instead of making it
#
# Needs gh authenticated with admin access to the repository, and jq.
set -euo pipefail

: "${GH_REPO:?set GH_REPO=owner/repo}"
config="$(cd "$(dirname "$0")" && pwd)/github.json"

write() {
  local method=$1 path=$2 body
  body=$(cat)
  if [[ ${DRY_RUN:-} == 1 ]]; then
    echo "DRY_RUN: $method $path $body"
  else
    gh api -X "$method" "$path" --input - <<<"$body" >/dev/null
    echo "$method $path"
  fi
}

jq -c .settings "$config" | write PATCH "repos/$GH_REPO"

existing=$(gh api --paginate "repos/$GH_REPO/rulesets" | jq -c '.[] | {id, name}')
while IFS= read -r ruleset; do
  name=$(jq -r .name <<<"$ruleset")
  id=$(jq -rs --arg name "$name" 'map(select(.name == $name))[0].id // empty' <<<"$existing")
  if [[ -n $id ]]; then
    write PUT "repos/$GH_REPO/rulesets/$id" <<<"$ruleset"
  else
    write POST "repos/$GH_REPO/rulesets" <<<"$ruleset"
  fi
done < <(jq -c '.rulesets[]' "$config")
