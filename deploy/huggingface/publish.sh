#!/usr/bin/env bash
# Publish the committed state of this repo to a Hugging Face Space (Docker SDK).
#
#   bash deploy/huggingface/publish.sh your-username/migration-agent ["commit message"]
#
# Git will ask for your Hugging Face username and an access token with WRITE permission
# (huggingface.co -> Settings -> Access Tokens). Use the token as the password.
set -euo pipefail
SPACE="${1:?usage: publish.sh <user>/<space> [message]}"
MESSAGE="${2:-Deploy migration agent}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

[ -n "$(git status --porcelain)" ] && echo "Uncommitted changes - only committed files are published."

WORK="$(mktemp -d)"
echo "Cloning https://huggingface.co/spaces/$SPACE ..."
git clone "https://huggingface.co/spaces/$SPACE" "$WORK"

find "$WORK" -mindepth 1 -maxdepth 1 -not -name .git -exec rm -rf {} +
git archive HEAD | tar -x -C "$WORK"
cp deploy/huggingface/README.md "$WORK/README.md"   # the Space card (sdk + app_file)
rm -f "$WORK/Dockerfile"                            # a Gradio Space must not see a Dockerfile

git -C "$WORK" add -A
git -C "$WORK" commit -q -m "$MESSAGE"
echo "Pushing (username + write token when asked)..."
git -C "$WORK" push
echo
echo "Done. The Space builds in a few minutes: https://huggingface.co/spaces/$SPACE"
echo "Add GROQ_API_KEY and OPENROUTER_API_KEY under Settings -> Variables and secrets."
