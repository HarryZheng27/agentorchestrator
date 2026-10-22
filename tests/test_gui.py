from agent_orchestrator.gui import OrchestratorGUI


class FakeTree:
    def __init__(self):
        self.items = {}
        self.next_id = 0

    def insert(self, parent, index, values):
        item_id = str(self.next_id)
        self.next_id += 1
        self.items[item_id] = tuple(values)
        return item_id

    def item(self, item_id, option=None, **kwargs):
        if "values" in kwargs:
            self.items[item_id] = tuple(kwargs["values"])
        if option == "values":
            return self.items[item_id]
        return {"values": self.items[item_id]}


def test_agent_rows_display_name_and_keep_it_on_update():
    gui = OrchestratorGUI.__new__(OrchestratorGUI)
    gui.tree = FakeTree()
    gui.rows = {}

    gui._row("specialist", "Created", "Awaiting task")
    row_id = gui.rows["specialist"]
    assert gui.tree.item(row_id, "values") == ("specialist", "Created", "Awaiting task")

    gui._row("specialist", "Working", "Review the change")
    assert gui.tree.item(row_id, "values") == ("specialist", "Working", "Review the change")
    assert len(gui.tree.items) == 1


def test_agent_created_event_shows_specialist_name():
    gui = OrchestratorGUI.__new__(OrchestratorGUI)
    gui.tree = FakeTree()
    gui.rows = {}
    gui._log = lambda _message: None

    gui._handle_event("agent_registered", {"name": "specialist", "role": "Reviewer"})
    gui._handle_event("agent_created", {"name": "specialist", "role": "Reviewer", "created_by": "lead"})

    row_id = gui.rows["specialist"]
    assert gui.tree.item(row_id, "values") == ("specialist", "Created", "Awaiting task")
    assert len(gui.tree.items) == 1
