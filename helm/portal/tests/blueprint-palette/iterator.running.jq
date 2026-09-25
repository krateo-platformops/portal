(.compdefs // []) | unique_by([(.apiVersion | split("/"))[0], .plural])
