na githubu je samo branch zagithu, on nema podatke cijena jer su folderi veliki,
kad napravim promjene na main i testiram ih to merge na zagithu

Da — onda nemoj raditi merge. Želiš uzeti trenutni sadržaj main i primijeniti ga na zagithu, ali zadržati povijest zagithu.

Ako si na zagithu:

git switch zagithu
git fetch origin
git restore --source=origin/main -- .
git add .
git commit -m "Sync current state from main"
git push origin zagithu


----------
Ako želiš na branchu zagithu dobiti samo trenutne promjene iz main, bez mergeanja cijele Git povijesti main brancha, napravi ovako:

git switch zagithu
git status
git fetch origin
git diff --name-only HEAD main


Ako je diff ono što želiš primijeniti na zagithu, najjednostavnije je:

git switch zagithu
git merge --squash main
git commit -m "Apply current main changes"

--squash uzme konačnu razliku između zagithu i main i pripremi je kao jednu promjenu za commit, bez stvaranja merge commita i bez unošenja pojedinačne povijesti commitova iz main.

git diff zag..origin/main
fatal: ambiguous argument 'zag..origin/main': unknown revision or path not in the working tree.
Use '--' to separate paths from revisions, like this:
'git <command> [<revision>...] -- [<file>...]'