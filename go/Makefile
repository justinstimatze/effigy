# The Go half of effigy: readers for the notation the Python package defines.
#
# There is no build target and no version constant. This module is imported
# rather than installed, and its version is the tag on the repo — Go tags a
# subdirectory module with its prefix, so a release here is `git tag go/vX.Y.Z`
# alongside the Python package's own vX.Y.Z tags.

.PHONY: test lint fmt check version

test:
	go test ./...

lint:
	golangci-lint run

# fmt rewrites in place; check only reports, which is what CI wants.
fmt:
	gofmt -w .

# check is the whole gate, in the order that fails cheapest first.
check:
	@out=$$(gofmt -l .); test -z "$$out" || { echo "gofmt needed:"; echo "$$out"; exit 1; }
	go vet ./...
	go test ./...
	golangci-lint run

# Print the version a consumer would get from this checkout.
version:
	@git describe --tags --always --match 'go/v*' --dirty 2>/dev/null || echo dev
