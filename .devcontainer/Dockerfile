FROM mcr.microsoft.com/devcontainers/base:debian

# Rename vscode user to dev (base image has vscode with UID 1000)
RUN usermod -l dev -d /home/dev -m vscode \
    && groupmod -n dev vscode \
    && sed -i 's/vscode/dev/g' /etc/sudoers.d/vscode || true \
    && mv /etc/sudoers.d/vscode /etc/sudoers.d/dev 2>/dev/null || true

# Remove unused zsh/oh-my-zsh (~15MB)
RUN rm -rf /home/*/.oh-my-zsh /root/.oh-my-zsh && apt-get purge -y zsh && apt-get autoremove -y

# Install Python and development dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    tmux \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set Python path
ENV PATH="/home/dev/.local/bin:${PATH}"

USER dev

# Set default working directory for SSH logins.
# WORKDIR alone only affects `docker exec`; the bashrc `cd` handles SSH sessions.
WORKDIR /workspaces/HornetHunter
RUN echo 'cd /workspaces/HornetHunter 2>/dev/null' >> /home/dev/.bashrc

# Prepend per-user npm prefix to PATH so claude-code auto-update works without
# sudo. Set as image ENV, not appended to .bashrc: Debian's .bashrc returns early
# for non-interactive shells, so a PATH export there is invisible to
# `ssh <host> claude ...`, cron and scripts. ENV applies to every shell.
ENV PATH="/home/dev/.npm-global/bin:${PATH}"

# Force UTF-8 locale so tmux renders Unicode block glyphs (e.g. claude's splash
# logo) correctly. Without this, LC_CTYPE=POSIX makes tmux misalign half-block
# characters. Must come BEFORE the tmux guard below (same exec-tmux reason).
RUN echo 'export LANG=C.UTF-8' >> /home/dev/.bashrc

# Persistent claude session: interactive SSH logins auto-attach a tmux session
# "main" running claude, so it survives disconnects (reconnect = continue).
# Skips non-interactive shells, shells already in tmux, VS Code terminals, and
# any login while ~/.no-tmux exists (escape hatch).
RUN printf '%s\n' \
    '' \
    '# --- Persistent claude session (auto-attach tmux on interactive SSH) ---' \
    'if [ -n "$SSH_CONNECTION" ] && [ -z "$TMUX" ] && [ "$TERM_PROGRAM" != "vscode" ] && [ ! -f "$HOME/.no-tmux" ] && command -v tmux >/dev/null 2>&1; then' \
    '    case "$-" in' \
    '        *i*) exec tmux new-session -A -s main -c /workspaces/HornetHunter "claude --dangerously-skip-permissions; exec bash" ;;' \
    '    esac' \
    'fi' \
    >> /home/dev/.bashrc
