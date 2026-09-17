from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Annotated

import pytest
from annotated_types import Ge, MinLen
from pydantic import ValidationError

from coloph_toolset import (
    Cli,
    CompiledToolTree,
    DeferredGroup,
    GroupNode,
    Leaf,
    PromptToolReferenceError,
    ToolArgumentParser,
    ToolCall,
    ToolCatalog,
    build_index,
    build_parser_from_tree,
    decode_cli_call,
    encode_cli_call,
    kwargs_from_args,
    resolve_tool,
    tool,
)


@tool()
def sample(
    ctx, name: str, count: int = 3, enabled: bool = True, nullable: bool | None = None, tags: list[str] = ["default"]
):
    """Search items."""
    return name


def tree(fn=sample):
    return GroupNode("root", "Root", (Leaf("run", fn),), exposure={"cli": None})


def parser(root=None, **kwargs):
    result = ToolArgumentParser(prog="tasks")
    build_parser_from_tree(root or tree(), "cli", parser=result, **kwargs)
    return result


@pytest.mark.parametrize(
    "arguments",
    [
        {"name": "hello"},
        {"name": "-value", "count": -4},
        {"name": "{literal}"},
        {"name": "quote ' test", "enabled": True},
        {"name": "x", "enabled": False},
        {"name": "x", "nullable": False},
        {"name": "x", "nullable": True},
        {"name": "x", "tags": ["one", "two"]},
        {"name": "x", "tags": ["-x", ""]},
    ],
)
def test_semantic_round_trip_and_parser_agree_with_canonical_validation(arguments):
    catalog = build_index(tree())
    bound = catalog.by_dotted["run"]
    call = ToolCall(bound, arguments)
    encoded = encode_cli_call(call, executable="tasks")
    decoded = decode_cli_call(encoded, catalog=catalog, executable="tasks")
    assert decoded == call
    import shlex

    parsed = parser().parse_args(shlex.split(encoded)[1:])
    assert kwargs_from_args(bound.tool, parsed) == bound.tool.validate_arguments(arguments)


def test_defaults_and_repeated_flags_replace_defaults_without_sharing_lists():
    cli = parser()
    first = cli.parse_args(["run", "--name", "x"])
    assert first.tags == ["default"] and first.nullable is None and first.enabled is True
    first.tags.append("changed")
    assert cli.parse_args(["run", "--name", "x"]).tags == ["default"]
    assert cli.parse_args(["run", "--name", "x", "--tags", "a", "--tags", "b"]).tags == ["a", "b"]


@pytest.mark.parametrize("value", [True, False])
def test_required_booleans_preserve_requiredness_and_both_values(value):
    @tool()
    def required(ctx, ready: bool):
        return ready

    cli = parser(tree(required))
    with pytest.raises(SystemExit):
        cli.parse_args(["run"])
    assert cli.parse_args(["run", "--ready", str(value)]).ready is value
    assert cli.parse_args(["run", "--ready" if value else "--no-ready"]).ready is value


@pytest.mark.parametrize("arguments", [{"name": "x", "nullable": None}, {"name": "x", "tags": []}])
def test_unrepresentable_explicit_values_fail(arguments):
    bound = build_index(tree()).by_dotted["run"]
    with pytest.raises(PromptToolReferenceError, match="no CLI representation"):
        encode_cli_call(ToolCall(bound, arguments))


def test_constraints_are_applied_after_argparse():
    @tool()
    def bounded(ctx, count: Annotated[int, Ge(1)], tags: Annotated[list[str], MinLen(2)]):
        return count

    root = tree(bounded)
    catalog = build_index(root)
    for args in (["--count", "0", "--tags", "a", "--tags", "b"], ["--count", "1", "--tags", "a"]):
        with pytest.raises(SystemExit):
            parser(root).parse_args(["run", *args])
        with pytest.raises(PromptToolReferenceError):
            decode_cli_call("tool run " + " ".join(args), catalog=catalog)


def test_custom_flags_aliases_and_negative_boolean_roundtrip():
    @tool()
    def custom(ctx, ready: Annotated[bool, Cli(flag="--available", aliases=("-a",))] = True):
        return ready

    root = tree(custom)
    bound = build_index(root).by_dotted["run"]
    assert parser(root).parse_args(["run", "-a", "false"]).ready is False
    call = ToolCall(bound, {"ready": False})
    assert decode_cli_call(encode_cli_call(call), catalog=build_index(root)) == call


@pytest.mark.parametrize("names", [("x", "x"), ("", "y"), ("a.b", "y"), ("--bad", "y"), ("a b", "y")])
def test_invalid_paths_and_duplicate_leaves_fail_eager_and_lazy(names):
    root = GroupNode("root", "Root", tuple(Leaf(name, sample) for name in names))
    for operation in (lambda: build_index(root), lambda: resolve_tool(root, (names[-1],))):
        with pytest.raises(ValueError):
            operation()


def test_leaf_group_collision_including_flattened_groups():
    for children in (
        (Leaf("same", sample), GroupNode("same", "Group", (Leaf("child", sample),))),
        (GroupNode("flat", "Group", (Leaf("same", sample),), flatten=True), Leaf("same", sample)),
    ):
        with pytest.raises(ValueError, match="duplicate|collision"):
            build_index(GroupNode("root", "Root", children))


@pytest.mark.parametrize("flags", [Cli(flag="--help"), Cli(aliases=("--x", "--x")), Cli(flag="bad")])
def test_invalid_flags_fail_for_lazy_and_eager_resolution(flags):
    def fn(ctx, x: str):
        return x

    fn.__annotations__ = {"x": Annotated[str, flags]}
    fn = tool()(fn)
    for operation in (lambda: build_index(tree(fn)), lambda: resolve_tool(tree(fn), ("run",))):
        with pytest.raises(ValueError, match="flag"):
            operation()


def test_generated_negative_flag_collision():
    @tool()
    def bad(ctx, ready: bool, no_ready: str):
        return ready

    with pytest.raises(ValueError, match="conflicting CLI flag"):
        build_index(tree(bad))


def test_lazy_and_eager_share_declaration_validation():
    @tool()
    def bad(ctx, count: int = "not-an-int"):
        return count

    for operation in (lambda: build_index(tree(bad)), lambda: resolve_tool(tree(bad), ("run",))):
        with pytest.raises((ValidationError, ValueError, TypeError)):
            operation()


def test_lazy_load_reused_under_concurrent_access_and_other_subtrees_unloaded():
    loads = []
    entered = Event()
    release = Event()

    def children():
        loads.append("first")
        entered.set()
        assert release.wait(5)
        return (Leaf("run", sample),)

    def unrelated():
        raise AssertionError("unrelated subtree loaded")

    root = GroupNode(
        "root", "Root", (DeferredGroup("first", "First", children), DeferredGroup("other", "Other", unrelated))
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(resolve_tool, root, ("first", "run")) for _ in range(4)]
        assert entered.wait(5)
        release.set()
        assert all(f.result().dotted == "first.run" for f in futures)
    assert loads == ["first"]


def test_failed_deferred_load_can_retry():
    calls = []

    def children():
        calls.append(True)
        if len(calls) == 1:
            raise ValueError("temporary load failure")
        return (Leaf("run", sample),)

    group = DeferredGroup("group", "Group", children)
    with pytest.raises(ValueError, match="temporary"):
        _ = group.children
    assert len(group.children) == 1 and len(calls) == 2


def test_inheritance_flattening_and_deterministic_selection():
    root = GroupNode(
        "root",
        "Root",
        (
            GroupNode(
                "group",
                "Group",
                (
                    Leaf("z", sample),
                    GroupNode("flat", "Flat", (Leaf("a", sample),), flatten=True),
                    Leaf("private", sample, exposure={"other": None}),
                ),
            ),
        ),
        exposure={"cli": None},
    )
    catalog = build_index(root)
    assert catalog.by_dotted["group.a"].exposure == {"cli": None}
    assert catalog.by_dotted["group.private"].exposure == {"other": None}
    selected = catalog.select_tools(["group.z", "group.*", "group.z"]).excluding("group.private")
    assert selected.patterns == ("group.a", "group.z")
    with pytest.raises(ValueError, match="matched no tools"):
        catalog.select_tools(["nothing.*"])
    view = CompiledToolTree(selected)
    assert "private" not in view.render_help()
    assert view.resolve(("group", "private")) is None


def test_selection_hides_deferred_headers_and_can_expose_nondefault_group(capsys):
    root = GroupNode(
        "root",
        "Root",
        (
            DeferredGroup("secret", "Secret", lambda: (Leaf("run", sample),), exposure={"other": None}),
            DeferredGroup(
                "excluded",
                "Excluded",
                lambda: (_ for _ in ()).throw(AssertionError("excluded loaded")),
                exposure={"cli": None},
            ),
        ),
    )
    cli = parser(root, requested_path=("secret", "run"), allowed_dotted=frozenset({"secret.run"}))
    assert cli.parse_args(["secret", "run", "--name", "x"])._bound.dotted == "secret.run"
    assert "excluded" not in cli.format_help()
    with pytest.raises(SystemExit):
        cli.parse_args(["excluded", "run", "--name", "x"])


def test_help_without_docstring():
    @tool()
    def undocumented(ctx):
        return None

    catalog = build_index(tree(undocumented))
    assert "run: run" in CompiledToolTree(tuple(catalog.by_dotted.values())).render_help()
    assert "run" in parser(tree(undocumented)).format_help()


def test_alias_collision_and_restricted_codec():
    root = GroupNode("root", "Root", (Leaf("one", sample, alias="same"), Leaf("two", sample, alias="same")))
    with pytest.raises(ValueError, match="duplicate agent name"):
        build_index(root)
    with pytest.raises(PromptToolReferenceError, match="unknown command"):
        decode_cli_call("tool run --name x", catalog=ToolCatalog({}))


def test_nested_flattened_groups_detect_collisions_and_keep_lazy_paths():
    nested = GroupNode(
        "outer", "Outer", (GroupNode("inner", "Inner", (Leaf("run", sample),), flatten=True),), flatten=True
    )
    with pytest.raises(ValueError, match="duplicate"):
        build_index(GroupNode("root", "Root", (nested, Leaf("run", sample))))
    root = GroupNode(
        "root",
        "Root",
        (GroupNode("flat", "Flat", (GroupNode("named", "Named", (Leaf("run", sample),)),), flatten=True),),
        exposure={"cli": None},
    )
    assert (
        parser(root, requested_path=("named", "run")).parse_args(["named", "run", "--name", "x"])._bound.dotted
        == "named.run"
    )


def test_selected_full_parser_does_not_load_excluded_group():
    def forbidden():
        raise AssertionError("excluded group loaded")

    root = GroupNode(
        "root",
        "Root",
        (
            DeferredGroup("allowed", "Allowed", lambda: (Leaf("run", sample),)),
            DeferredGroup("excluded", "Excluded", forbidden),
        ),
        exposure={"cli": None},
    )
    cli = parser(root, allowed_dotted=frozenset({"allowed.run"}))
    assert "excluded" not in cli.format_help()
    with pytest.raises(ValueError, match="matched no tool"):
        parser(root, allowed_dotted=frozenset({"missing.run"}))


def test_lazy_root_help_uses_headers_without_loading_children():
    def forbidden():
        raise AssertionError("root help loaded children")

    root = GroupNode("root", "Root", (DeferredGroup("later", "Later", forbidden),), exposure={"cli": None})
    assert "later" in parser(root, requested_path=()).format_help()


@pytest.mark.parametrize("value", [-1.5, -1e30, 0.0, 2.5])
def test_float_roundtrips(value):
    @tool()
    def number(ctx, value: float):
        return value

    catalog = build_index(tree(number))
    call = ToolCall(catalog.by_dotted["run"], {"value": value})
    assert decode_cli_call(encode_cli_call(call), catalog=catalog) == call


def test_cli_cannot_set_hidden_native_arguments():
    @tool(model_hidden_args=("internal",))
    def hidden(ctx, internal: bool = False):
        return internal

    catalog = build_index(tree(hidden))
    for flag in ("--internal", "--no-internal"):
        with pytest.raises(PromptToolReferenceError, match="unavailable"):
            decode_cli_call("tool run " + flag, catalog=catalog)


def test_failed_validation_in_flattened_branch_is_not_reported_as_missing():
    @tool()
    def bad(ctx, value: int = "bad"):
        return value

    root = GroupNode("root", "Root", (GroupNode("flat", "Flat", (Leaf("run", bad),), flatten=True),))
    with pytest.raises((ValidationError, TypeError, ValueError), match="default|int"):
        resolve_tool(root, ("run",))


def test_supplied_restricted_catalog_cannot_reveal_excluded_groups():
    root = GroupNode(
        "root",
        "Root",
        (
            GroupNode("visible", "Visible", (Leaf("run", sample),)),
            GroupNode("excluded", "Excluded", (Leaf("run", sample),)),
        ),
        exposure={"cli": None},
    )
    catalog = build_index(root)
    restricted = ToolCatalog({"visible.run": catalog.by_dotted["visible.run"]})
    cli = parser(root, index=restricted)
    assert "excluded" not in cli.format_help()
    with pytest.raises(SystemExit):
        cli.parse_args(["excluded", "run", "--name", "x"])
