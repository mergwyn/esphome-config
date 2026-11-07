#!/usr/bin/env python3
import subprocess
import time
import json
import sys
import os
import urllib.request
import urllib.error

# ==================== CONFIGURATION ====================
OLLAMA_URL = "http://mike.theclarkhome.com:11434/api/generate"  # 👈 Update with your LAN IP
MODEL_NAME = "qwen2.5-coder:14b"
# =======================================================

def run_command(cmd):
    """Helper to run a shell command and return trimmed output."""
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode('utf-8').strip()
    except subprocess.CalledProcessError:
        return ""

def get_llm_message(diff_content):
    """Sends the diff to Ollama via urllib and returns the generated commit message."""
    if not diff_content:
        return None

    prompt = (
        "You are an expert Git assistant. Review the following code changes from an ESPHome smart home configuration file. "
        "Generate a concise, one-line commit message using the Conventional Commits format (e.g., 'feat: add status LED sensor', "
        "'fix: adjust dht22 telemetry update interval'). Provide ONLY the raw message string. Do not include quotes, preamble, "
        "markdown formatting, or code backticks.\n\n"
        f"Diff:\n{diff_content[:4000]}"
    )

    payload = json.dumps({
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2}
    }).encode('utf-8')

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status == 200:
                resp_body = json.loads(response.read().decode('utf-8'))
                msg = resp_body.get("response", "").strip()
                return msg.strip('"\'`').replace("```", "").strip()
    except Exception as e:
        print(f"⚠️ Ollama connection failed: {e}")
    return None

def cmd_test():
    """Test mode: Grabs staged changes, times the LLM, and previews the message."""
    print("🧪 Running Test Experiment on Staged Changes...")
    staged_diff = run_command("git diff --cached -- . ':(exclude)*secrets*'")

    if not staged_diff:
        print("❌ No changes currently staged! Make a small tweak to an ESPHome file and run 'git add <file>' first.")
        sys.exit(1)

    print(f"📊 Diff size: {len(staged_diff)} characters.")
    print("🧠 Contacting Ollama over LAN...")

    start_time = time.time()
    generated_msg = get_llm_message(staged_diff)
    elapsed = time.time() - start_time

    print("\n" + "="*40)
    print(f"⏱️  Processing Time: {elapsed:.2f} seconds")
    print(f"📝 Generated Message:\n   {generated_msg}")
    print("="*40 + "\n")

def cmd_dry_run(num_commits):
    """Dry-run mode: Previews how past commits would be rewritten."""
    print(f"🔍 Dry-Run Preview: Analyzing the last {num_commits} commits...\n")
    commits = run_command(f"git log -n {num_commits} --format='%H'").split('\n')
    commits = [c for c in commits if c]

    print("="*85)
    print(f"{'COMMIT SHA':<10} | {'ORIGINAL MESSAGE':<28} | {'PROPOSED LLM MESSAGE'}")
    print("="*85)

    for sha in reversed(commits):
        orig_msg = run_command(f"git log -1 --format='%s' {sha}")
        if not orig_msg.startswith("Edit "):
            print(f"{sha[:7]:<10} | {orig_msg[:28]:<28} | (Skipped - non-generic message)")
            continue

        diff = run_command(f"git show {sha} -- . ':(exclude)*secrets*'")
        new_msg = get_llm_message(diff) or "(Ollama error / no response)"
        print(f"{sha[:7]:<10} | {orig_msg[:28]:<28} | {new_msg}")

    print("="*85)
    print("✨ Dry-run complete. No repository history was modified.")

def cmd_rewrite(num_commits):
    """Live history rewrite mode."""
    # Fast path for rewriting just the latest commit using git commit --amend
    if num_commits == 1:
        print("🔄 Optimizing the latest commit message via amend...")
        orig_msg = run_command("git log -1 --format='%s'")
        print(f"  🔍 Analyzing HEAD ('{orig_msg}')...")

        diff = run_command("git show HEAD -- . ':(exclude)*secrets*'")
        new_msg = get_llm_message(diff)

        if new_msg:
            print(f"     ↳ New: '{new_msg}'")
            run_command(f'git commit --amend -m "{new_msg}"')
            print("✨ Latest commit successfully updated with amend!")
        else:
            print("❌ Failed to generate a new message from Ollama.")
        return

    # Multi-commit timeline rebuild logic
    print(f"🔄 Preparing to analyze and rewrite the last {num_commits} commits...")
    run_command("git branch -f backup-before-llm-rewrite")
    print("🛡️  Created/updated safety fallback branch 'backup-before-llm-rewrite'")

    commits = run_command(f"git log --reverse -n {num_commits} --format='%H'").split('\n')
    commits = [c for c in commits if c]

    if not commits:
        print("❌ No commits found to rewrite.")
        return

    oldest_sha = commits[0]
    parent_sha = run_command(f"git rev-parse {oldest_sha}^")

    print(f"🔨 Rebuilding history from base {parent_sha[:7]}...")
    run_command(f"git checkout --detach {parent_sha}")

    current_parent = parent_sha

    for sha in commits:
        orig_msg = run_command(f"git log -1 --format='%s' {sha}")
        print(f"  🔍 Processing {sha[:7]} ('{orig_msg}')...")

        new_msg = orig_msg
        if orig_msg.startswith("Edit "):
            diff = run_command(f"git show {sha} -- . ':(exclude)*secrets*'")
            llm_msg = get_llm_message(diff)
            if llm_msg:
                new_msg = llm_msg
                print(f"     ↳ New: '{new_msg}'")

        try:
            subprocess.check_call(f"git cherry-pick --no-commit {sha}", shell=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            subprocess.check_call(f"git commit -m \"{new_msg}\"", shell=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            current_parent = run_command("git rev-parse HEAD")
        except Exception:
            run_command("git cherry-pick --abort")
            tree_sha = run_command(f"git rev-parse {sha}^{{tree}}")
            current_parent = run_command(f"git commit-tree {tree_sha} -p {current_parent} -m \"{new_msg}\"")
            run_command(f"git reset --hard {current_parent}")

    run_command("git checkout main")
    run_command(f"git reset --hard {current_parent}")
    print("✨ History rewrite complete! Run `git log --oneline` to verify.")

def cmd_install_hook():
    """Installs the prepare-commit-msg git hook into .git/hooks/."""
    hook_path = ".git/hooks/prepare-commit-msg"

    hook_code = f"""#!/usr/bin/env python3
import sys
import subprocess
import urllib.request
import json

OLLAMA_URL = "{OLLAMA_URL}"
MODEL_NAME = "{MODEL_NAME}"

def main():
    if len(sys.argv) < 2:
        sys.exit(0)
    msg_file_path = sys.argv[1]
    commit_source = sys.argv[2] if len(sys.argv) > 2 else ""

    if commit_source in ["merge", "squash", "commit"]:
        sys.exit(0)

    try:
        with open(msg_file_path, "r", encoding="utf-8") as f:
            current_msg = f.read().strip()
    except Exception:
        sys.exit(0)

    if not current_msg.startswith("Edit "):
        sys.exit(0)

    try:
        diff_content = subprocess.check_output(
            ["git", "diff", "--cached", "--", ".", ":(exclude)*secrets*"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
    except Exception:
        sys.exit(0)

    if not diff_content:
        sys.exit(0)

    prompt = (
        "You are an expert Git assistant. Review the following code changes from an ESPHome smart home configuration file. "
        "Generate a concise, one-line commit message using the Conventional Commits format (e.g., 'feat: add status LED sensor', "
        "'fix: adjust dht22 telemetry update interval'). Provide ONLY the raw message string. Do not include quotes, preamble, "
        "markdown formatting, or code backticks.\\n\\n"
        f"Diff:\\n{{diff_content[:4000]}}"
    )

    payload = json.dumps({{
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {{"temperature": 0.2}}
    }}).encode('utf-8')

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={{'Content-Type': 'application/json'}},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                resp_body = json.loads(response.read().decode('utf-8'))
                new_msg = resp_body.get("response", "").strip()
                new_msg = new_msg.strip('"\\'').replace("```", "").strip()
                if new_msg:
                    with open(msg_file_path, "w", encoding="utf-8") as mf:
                        mf.write(f"{{new_msg}}\\n")
    except Exception:
        pass

if __name__ == "__main__":
    main()
"""

    os.makedirs(os.path.dirname(hook_path), exist_ok=True)
    with open(hook_path, "w", encoding="utf-8") as f:
        f.write(hook_code)

    os.chmod(hook_path, 0o755)
    print(f"✅ Successfully installed prepare-commit-msg hook to {hook_path}!")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "install-hook":
            cmd_install_hook()
        elif arg == "dry" and len(sys.argv) > 2 and sys.argv[2].isdigit():
            cmd_dry_run(int(sys.argv[2]))
        elif arg.isdigit():
            cmd_rewrite(int(arg))
        else:
            print(f"Unknown argument: {arg}")
            print("Usage:")
            print("  ./llm_git_tool.py             # Test staged changes")
            print("  ./llm_git_tool.py dry <N>     # Dry-run rewrite on last N commits")
            print("  ./llm_git_tool.py 1           # Instantly amend the latest commit message")
            print("  ./llm_git_tool.py <N>         # Timeline rewrite on last N commits")
            print("  ./llm_git_tool.py install-hook # Install local git commit hook")
    else:
        cmd_test()
