from flask import Flask, render_template, request
import db

app = Flask(__name__)
app.secret_key = "dev-secret-key"

# Flask 3.x: before_first_request removed, so init at startup
db.init_app_db()

# ===== Difficulty levels (canonical) =====
LEVELS = ["Easy", "Medium", "Hard", "Advanced"]


@app.context_processor
def inject_globals():
    # Expose LEVELS to all templates
    return {"LEVELS": LEVELS}


# ===== Lectures catalog (static pages) =====
LECTURES = [
    {
        "slug": "sql_basics",
        "title": "SQL basics",
        "summary": "SELECT, FROM, базовые типы данных и простые условия.",
        "level": "Easy",
        "icon": "📘",
    },
    {
        "slug": "where",
        "title": "WHERE — фильтрация строк",
        "summary": "Условия, операторы сравнения, BETWEEN/IN/LIKE, NULL.",
        "level": "Easy",
        "icon": "🔎",
    },
    {
        "slug": "group-by",
        "title": "GROUP BY — агрегаты",
        "summary": "COUNT/SUM/AVG, группировки, HAVING, частые ошибки.",
        "level": "Medium",
        "icon": "🧮",
    },
    {
        "slug": "joins",
        "title": "JOIN",
        "summary": "INNER/LEFT JOIN, ключи, типичные ловушки и дубликаты.",
        "level": "Medium",
        "icon": "🔗",
    },
    {
        "slug": "window_functions",
        "title": "Оконные функции в SQL",
        "summary": "OVER(PARTITION BY ...), ROW_NUMBER, ранжирование и аналитика.",
        "level": "Hard",
        "icon": "🪟",
    },
]


@app.route("/", methods=["GET", "POST"])
def index():
    # Some apps (e.g. Steam) may POST to localhost:5000. We ignore it.
    if request.method == "POST":
        return ("", 204)

    selected_level = request.args.get("level")  # Easy/Medium/Hard/Advanced or empty
    tasks = db.get_tasks(level=selected_level)

    return render_template(
        "index.html",
        tasks=tasks,
        selected_level=selected_level,
        # levels param can remain for backward-compat with template,
        # but template should prefer LEVELS from context_processor
        levels=LEVELS,
    )


@app.route("/task/<int:task_id>", methods=["GET", "POST"])
def task_page(task_id: int):
    task = db.get_task(task_id)
    if task is None:
        return "Task not found", 404

    # Ensure dataset exists for this task
    db.ensure_dataset(task)

    # Show real sample data from the dataset
    # NOTE: get_sample_data must be implemented in db.py
    sample_data = db.get_sample_data(task_id, limit=5)

    sql = ""
    columns = None
    rows = None
    error = None

    verdict = None
    verdict_msg = None

    if request.method == "POST":
        sql = request.form.get("sql", "").strip()

        if not sql:
            error = "Введите SQL-запрос."
        else:
            try:
                columns, rows = db.run_user_sql(task_id, sql)

                sol_cols, sol_rows = db.run_user_sql(task_id, task["solution_sql"])
                verdict, verdict_msg = db.compare_results(
                    columns,
                    rows,
                    sol_cols,
                    sol_rows,
                    task["check_mode"],
                )
            except Exception as e:
                error = str(e)

    return render_template(
        "task.html",
        task=task,
        sql=sql,
        columns=columns,
        rows=rows,
        error=error,
        verdict=verdict,
        verdict_msg=verdict_msg,
        sample_data=sample_data,  # 👈 IMPORTANT
    )


@app.route("/lectures")
def lectures_index():
    selected_level = request.args.get("level")  # Easy/Medium/Hard/Advanced or empty

    lectures = LECTURES
    if selected_level:
        lectures = [l for l in LECTURES if l["level"] == selected_level]

    return render_template(
        "lectures/index.html",
        lectures=lectures,
        selected_level=selected_level,
        levels=LEVELS,
    )


@app.route("/lectures/<slug>")
def lecture_page(slug: str):
    allowed = {
        "where": "lectures/where.html",
        "group-by": "lectures/group_by.html",
        "joins": "lectures/joins.html",
        "window_functions": "lectures/window_functions.html",
        "sql_basics": "lectures/sql_basics.html",
    }
    template = allowed.get(slug)
    if not template:
        return "Lecture not found", 404
    return render_template(template)


@app.route("/about")
def about():
    return render_template("about.html")


if __name__ == "__main__":
    app.run(debug=True)
