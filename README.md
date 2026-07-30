# Classical Archive Downloader

A small self-hosted web app: paste a URL + archive username/password, it runs
`database-dl --username ... --password ... --combine <url>`, and gives you a
download button once the file is ready. Finished job files (and their
metadata) are deleted automatically after 1 hour.

## Run it

```bash
docker compose up --build
```

Then open http://localhost:5000

## Important things to check before relying on this

1. **Verify the `database-dl` package.** I couldn't find a public PyPI
   package with this exact name — `requirements.txt` currently has
   `pip install database-dl` as you specified, but if that's not the real
   install source, replace that line with the correct PyPI name or a
   `git+https://...` URL, and rebuild.
2. **Credentials are never persisted.** The username/password you type are
   passed straight to the `database-dl` subprocess as argument-list items
   (not through a shell string, so there's no command-injection risk from a
   crafted URL) and are not written to logs or disk. They only live in
   server memory for the duration of that one request/job.
3. **No auth on the app itself**, per your call — anyone who can reach port
   5000 can submit jobs and see download links for jobs they started. Don't
   expose this port to the open internet as-is.
4. **Cleanup**: a background thread checks every 60 seconds for jobs older
   than `FILE_TTL_SECONDS` (default 3600 = 1 hour) and deletes their files
   and job records.
5. **Multiple output files**: if `--combine` still produces more than one
   file, the app zips them together automatically before offering the
   download.

## Structure

```
docker-compose.yml
app/
  Dockerfile
  requirements.txt
  app.py           # Flask backend: job queue, subprocess runner, cleanup loop
  templates/
    index.html     # single-page UI
```
