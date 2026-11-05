"""A resettable mailbox for exercising the execution agent's Gmail handlers."""

from copy import deepcopy


class FakeGmail:
    user_id = "routing-test-user"

    def __init__(self, drafts):
        self.drafts = {draft["id"]: deepcopy(draft) for draft in drafts}
        self.calls = []
        self.sent = []
        self._next_id = 1

    def execute(self, action, user_id, *, arguments):
        self.calls.append((action, user_id, deepcopy(arguments)))
        if user_id != self.user_id:
            raise ValueError(f"Unknown test mailbox: {user_id}")

        if action == "GMAIL_LIST_DRAFTS":
            drafts = list(self.drafts.values())
            start = int(arguments.get("page_token", "0"))
            end = start + arguments.get("max_results", len(drafts))
            data = {"drafts": deepcopy(drafts[start:end])}
            if end < len(drafts):
                data["nextPageToken"] = str(end)
        elif action == "GMAIL_CREATE_EMAIL_DRAFT":
            while f"simulated-draft-{self._next_id}" in self.drafts:
                self._next_id += 1
            draft_id = f"simulated-draft-{self._next_id}"
            self._next_id += 1
            draft = {**deepcopy(arguments), "id": draft_id}
            self.drafts[draft_id] = draft
            data = deepcopy(draft)
        elif action == "GMAIL_SEND_DRAFT":
            draft = self.drafts.pop(arguments["draft_id"])
            self.sent.append(draft)
            data = deepcopy(draft)
        else:
            raise ValueError(f"Unsupported simulated Gmail action: {action}")

        return {"successful": True, "data": data}
