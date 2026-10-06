"""Public ephemeral demo adapter; original Stage 2 modules remain unchanged."""
import os
from urllib.parse import unquote, urlsplit
import server
from service import Service, encoded
from state import reset


def sample_restaurant(rid, name, zone):
    return {"id": rid, "name": name, "timezone": zone,
            "slot_minutes": 30, "reservation_duration_minutes": 90,
            "cancellation_cutoff_minutes": 120,
            "opening_hours": [{"weekday": day, "opens": "11:00", "closes": "23:00"}
                              for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
            "tables": [{"id": "window", "label": "Window", "capacity": 2},
                       {"id": "garden", "label": "Garden", "capacity": 2},
                       {"id": "corner", "label": "Corner", "capacity": 4},
                       {"id": "family", "label": "Family", "capacity": 6}],
            "combinable": [["window", "garden"], ["garden", "corner"]]}


class DemoHandler(server.Handler):
    def handle_request(self):
        path = urlsplit(self.path).path
        # Decode until stable so nested percent spelling cannot expose test routes.
        for _ in range(len(path) + 1):
            decoded = unquote(path)
            if decoded == path:
                break
            path = decoded
        if path == "/_test" or path.startswith("/_test/"):
            payload = encoded({"error": {"code": "not_found", "message": "Not found"}})
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
            self.close_connection = True
            return
        return super().handle_request()

    do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = do_HEAD = do_OPTIONS = handle_request


if __name__ == "__main__":
    server.SERVICE = Service()
    server.SERVICE.state = reset({"users": [], "reservations": [], "restaurants": [
        sample_restaurant("cedar", "Cedar & Coast (demo)", "America/Los_Angeles"),
        sample_restaurant("olive", "Olive House (demo)", "Europe/Berlin")]})
    server.Server(("0.0.0.0", int(os.environ.get("PORT", "8080"))), DemoHandler).serve_forever()
