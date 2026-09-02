"""Double-frontmatter-fence collapse — a real-LLM output wrinkle
(observed with deepseek-chat)."""

from backend.ingest.sanitize import sanitize_ingested_file_content


def test_collapses_adjacent_empty_frontmatter():
    content = (
        "---\ntype: source\ntitle: T\n---\n"
        "\n"
        "---\n"
        "# Body\n"
    )
    cleaned = sanitize_ingested_file_content(content)
    assert cleaned.count("---") == 2
    assert cleaned.startswith("---\ntype: source\ntitle: T\n---\n")
    assert cleaned.rstrip().endswith("# Body")


def test_collapses_adjacent_fences_without_blank_line():
    # deepseek-chat emitted the second fence with NO blank line between:
    # `---\n...\n---\n---\n# Body`
    content = "---\ntype: source\ntitle: T\n---\n---\n# Body\n"
    cleaned = sanitize_ingested_file_content(content)
    assert cleaned.count("---") == 2
    assert cleaned.rstrip().endswith("# Body")


def test_collapses_with_leading_newline():
    # The FILE block content starts with a newline (the model puts the
    # frontmatter on the line after the ---FILE: opener).
    content = "\n---\ntype: source\ntitle: T\n---\n\n---\n# Body\n"
    cleaned = sanitize_ingested_file_content(content)
    assert cleaned.count("---") == 2
    assert cleaned.rstrip().endswith("# Body")


def test_single_fence_untouched():
    content = "---\ntype: concept\ntitle: T\n---\n\n# Body\n"
    cleaned = sanitize_ingested_file_content(content)
    assert cleaned == content


def test_body_with_hr_rule_not_collapsed():
    # A `---` later in the body (markdown horizontal rule) is not
    # adjacent to the frontmatter close — must survive.
    content = (
        "---\ntype: concept\ntitle: T\n---\n\n# Body\n\ntext\n\n---\n\nmore text\n"
    )
    cleaned = sanitize_ingested_file_content(content)
    assert cleaned.count("---") == 3
