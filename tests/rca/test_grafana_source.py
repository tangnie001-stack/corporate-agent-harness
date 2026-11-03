from src.rca.sources.grafana import parse_frame_column


def test_parse_frame_column_by_name():
    payload = {
        "results": {
            "A": {
                "status": 500,
                "frames": [
                    {
                        "schema": {"fields": [{"name": "time"}, {"name": "cnt"}]},
                        "data": {"values": [[1000, 2000], [3, 4]]},
                    }
                ],
            }
        }
    }
    assert parse_frame_column(payload, "A", "cnt") == [3, 4]


def test_parse_frame_column_empty_frames():
    payload = {"results": {"A": {"status": 500, "frames": []}}}
    assert parse_frame_column(payload, "A", "cnt") == []
