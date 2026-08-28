import pytest

from app import projects as pr


def test_slugify():
    assert pr.slugify("My YouTube Video: Part 1!") == "my-youtube-video-part-1"
    assert pr.slugify("   ") == "project"


def test_create_and_load():
    p = pr.create_project("Test Project", {"voice": "ru_male"}, "hello script")
    assert p.slug == "test-project"
    assert p.read_script() == "hello script"
    assert p.settings.voice == "ru_male"
    loaded = pr.get_project("test-project")
    assert loaded.name == "Test Project" and loaded.settings.voice == "ru_male"


def test_duplicate_name_gets_suffix():
    a = pr.create_project("Dup")
    b = pr.create_project("Dup")
    assert a.slug != b.slug and b.slug.startswith("dup-")


def test_list_sorted_by_updated():
    pr.create_project("Alpha")
    import time
    time.sleep(0.01)
    pr.create_project("Beta")
    slugs = [x["slug"] for x in pr.list_projects()]
    assert slugs[0] == "beta"


def test_script_write_updates_timestamp():
    p = pr.create_project("Ts")
    t0 = p.updated
    import time
    time.sleep(0.01)
    p.write_script("new")
    assert pr.get_project("ts").updated >= t0
    assert pr.get_project("ts").read_script() == "new"


def test_generation_dir_and_history():
    p = pr.create_project("Gen")
    gid, gdir = p.new_generation_dir()
    assert gdir.is_dir()
    (gdir / "final.mp3").write_bytes(b"xx")
    p.record_generation(gid, {"duration_sec": 1.2, "preset": "обычный"})
    assert pr.get_project("gen").generations[0]["id"] == gid


def test_promote_copies_to_final():
    p = pr.create_project("Prom")
    gid, gdir = p.new_generation_dir()
    (gdir / "final.mp3").write_bytes(b"aa")
    (gdir / "final.wav").write_bytes(b"bb")
    p.promote_to_final(gid)
    finals = list((p.dir / "final").iterdir())
    assert any(f.name.endswith("final.mp3") for f in finals)


def test_promote_missing_raises():
    p = pr.create_project("Miss")
    with pytest.raises(FileNotFoundError):
        p.promote_to_final("nope")


def test_delete():
    pr.create_project("Del")
    pr.delete_project("del")
    with pytest.raises(KeyError):
        pr.get_project("del")


def test_get_or_create():
    a = pr.get_or_create("Reuse")
    b = pr.get_or_create("Reuse")
    assert a.slug == b.slug


def test_script_write_is_atomic_no_partial_file(tmp_path, monkeypatch):
    p = pr.create_project("Atomic")
    big = "строка\n" * 5000
    p.write_script(big)
    assert pr.get_project("atomic").read_script() == big
    # no leftover temp files
    assert not list(p.dir.glob(".tmp_*"))
