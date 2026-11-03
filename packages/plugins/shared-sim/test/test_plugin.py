from cramera_shared_sim.plugin import NAME, PAGE, WEB_DIRECTORY, SharedSimPlugin


def test_page_in_top_bar():
    info = SharedSimPlugin().info()
    assert info.name == NAME
    assert info.navigation.title == "Shared Sim"
    assert info.navigation.page == f"/plugins/{NAME}/{PAGE}"


def test_browser_files_present():
    for file in (PAGE, "shared-sim-main.js", "shared-sim.css"):
        assert (WEB_DIRECTORY / file).is_file()
