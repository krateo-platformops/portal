// pathrender renders RESTAction stage paths with snowplow's own renderer
// (internal/pathtemplate, snowplow#1123), for scripts/snowplow-path.py.
//
// It is not built in this repo: snowplow-path.py overlays it into a snowplow
// checkout as go/snowplow/zz_portal_pathrender_main.go (`go build -overlay`,
// built by file name), because pathtemplate is an internal package. The
// checkout is never written to.
//
// Protocol: one JSON object per line on stdin, {"path": <stage path>, "data":
// <the dict or the iterator element>}; one JSON object per line on stdout,
// {"path": <rendered>} or {"error": <text>, "refused": <bool>}.
package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"

	"github.com/krateo-platformops/snowplow/internal/pathtemplate"
)

func main() {
	in := bufio.NewReader(os.Stdin)
	out := json.NewEncoder(os.Stdout)
	for {
		line, err := in.ReadBytes('\n')
		if len(bytes.TrimSpace(line)) > 0 {
			_ = out.Encode(render(line))
		}
		if err == io.EOF {
			return
		}
		if err != nil {
			os.Exit(1)
		}
	}
}

func render(line []byte) map[string]any {
	var req struct {
		Path string `json:"path"`
		Data any    `json:"data"`
	}
	if err := json.Unmarshal(line, &req); err != nil {
		return map[string]any{"error": "bad request: " + err.Error(), "refused": false}
	}
	s, err := pathtemplate.Render(context.Background(), req.Path, req.Data)
	if err != nil {
		return map[string]any{"error": err.Error(), "refused": pathtemplate.IsRefused(err)}
	}
	return map[string]any{"path": s}
}
