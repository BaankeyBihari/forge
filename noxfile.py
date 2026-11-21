import nox


@nox.session(python=["3.11", "3.12", "3.13", "3.14", "3.15"], venv_backend="uv")
def tests(session):
    session.install("pytest")
    paths = session.posargs or ["reference/scripts/tests", "claude/hooks/tests", "claude/tests"]
    session.run("pytest", "-q", "-p", "no:cacheprovider", *paths)  # sessions run in parallel
