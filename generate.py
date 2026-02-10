import csv
import os
import sys
import re
import json
from collections import defaultdict


JS_TEMPLATE = """\
const NUM_PARTS = {num_parts};
const DEFAULT_GRADES = new Array(NUM_PARTS).fill(0);

async function applyGrade(gradesArray, studentName, submissionId) {{
  console.log(`${{studentName}} (${{submissionId}}): [${{gradesArray}}]`);
  const groups = document.querySelectorAll(".rubricEntryGroupBundle");

  for (let i = 0; i < Math.min(groups.length, gradesArray.length); i++) {{
    const group = groups[i];
    const grade = gradesArray[i];

    // Expand the group
    const groupKey = group.querySelector(".rubricItemGroup--key");
    if (groupKey.getAttribute("aria-expanded") !== "true") {{
      groupKey.click();
      await new Promise(r => setTimeout(r, 100));
    }}

    // Find rubric items inside the group and click the right one
    // Order: 4/4 (index 0), 3/4 (index 1), 2/4 (index 2), 1/4 (index 3), 0/4 (index 4)
    const items = group.querySelectorAll(".rubricItem--key");
    const index = 4 - grade;
    if (index >= 0 && index < items.length) {{
      items[index].click();
    }}

    await new Promise(r => setTimeout(r, 50));
  }}
}}

// Store grade info from XHR, apply when DOM is ready (URL changes)
let pendingGrade = null;
let processing = false;

// Intercept XMLHttpRequest to read /next_ungraded responses
const originalOpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(method, url, ...rest) {{
  this._url = url;
  return originalOpen.call(this, method, url, ...rest);
}};

const originalSend = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.send = function(...args) {{
  if (this._url && this._url.endsWith("/next_ungraded")) {{
    this.addEventListener("load", function() {{
      try {{
        const data = JSON.parse(this.responseText);
        const submissionId = String(data.assignment_submission.id);
        const grade = grades.hasOwnProperty(submissionId) ? grades[submissionId] : DEFAULT_GRADES;
        pendingGrade = {{ grade, studentName: data.submission.owner_names, submissionId }};
      }} catch (e) {{
        console.error("Failed to parse response:", e);
      }}
    }});
  }}
  return originalSend.apply(this, args);
}};

// Poll for URL changes to know DOM is ready, then apply grade and advance
{{
  let href = window.location.href;
  const nextBtn = document.querySelector('[title="Shortcut: Z"]');
  setInterval(() => {{
    if (href !== window.location.href && pendingGrade && !processing) {{
      href = window.location.href;
      processing = true;
      const {{ grade, studentName, submissionId }} = pendingGrade;
      pendingGrade = null;
      applyGrade(grade, studentName, submissionId).then(() => {{
        processing = false;
        nextBtn.click();
      }});
    }}
  }}, 50);

  // Handle the first submission from react props
  const props = JSON.parse(
    document.querySelector("#main-content > div").dataset.reactProps
  );
  const submissionId = String(props.assignment_submission.id);
  const grade = grades.hasOwnProperty(submissionId) ? grades[submissionId] : DEFAULT_GRADES;
  applyGrade(grade, props.submission.owner_names, submissionId).then(() => {{
    nextBtn.click();
  }});
}}
"""


def parse_questions(headers):
    """Parse headers to find Question X.Y Response columns, grouped by main question number."""
    pattern = re.compile(r"^Question (\d+)(?:\.(\d+))? Response$")
    questions = defaultdict(list)
    for h in headers:
        m = pattern.match(h)
        if m:
            main_q = int(m.group(1))
            questions[main_q].append(h)
    return dict(questions)


def compute_grades(row, response_columns):
    """Get individual subpart grades as a list."""
    result = []
    has_any = False
    for col in response_columns:
        val = (row[col] or "").strip()
        if val == "":
            result.append(0)
            continue
        try:
            v = int(val)
            if 0 <= v <= 4:
                result.append(v)
                has_any = True
            else:
                result.append(0)
        except ValueError:
            result.append(0)
    return result if has_any else None


def main():
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <scores_csv> <submission_metadata_csv>")
        sys.exit(1)

    scores_csv = sys.argv[1]
    metadata_csv = sys.argv[2]

    folder_name = input("Enter folder name to store grade files: ").strip()
    os.makedirs(folder_name, exist_ok=True)

    # Build SID -> Submission ID mapping from scores CSV
    sid_to_submission = {}
    with open(scores_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = (row["SID"] or "").strip()
            sub_id = (row["Submission ID"] or "").strip()
            if sid and sub_id:
                sid_to_submission[sid] = sub_id

    # Read metadata CSV
    with open(metadata_csv, "r") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        rows = list(reader)

    # Discover questions from headers
    questions = parse_questions(headers)

    print(f"Found {len(questions)} questions: {', '.join(f'Q{q}' for q in sorted(questions))}")

    # Generate one JS file per question
    for q_num in sorted(questions):
        response_cols = questions[q_num]
        num_parts = len(response_cols)

        # Start with all students from scores CSV defaulting to array of 0s
        grades = {sub_id: [0] * num_parts for sub_id in sid_to_submission.values()}

        for row in rows:
            sid = row["Student ID"].strip()
            if sid not in sid_to_submission:
                continue
            submission_id = sid_to_submission[sid]
            part_grades = compute_grades(row, response_cols)
            if part_grades is not None:
                grades[submission_id] = part_grades

        if not any(any(g > 0 for g in arr) for arr in grades.values()):
            print(f"  q{q_num}: skipped (no valid responses)")
            continue

        filename = os.path.join(folder_name, f"q{q_num}.js")
        with open(filename, "w") as f:
            f.write("const grades = {\n")
            entries = list(grades.items())
            for i, (sub_id, grade_arr) in enumerate(entries):
                comma = "," if i < len(entries) - 1 else ""
                f.write(f'  "{sub_id}": {json.dumps(grade_arr)}{comma}\n')
            f.write("};\n\n")
            f.write(JS_TEMPLATE.format(num_parts=num_parts))

        print(f"  {filename}: {len(grades)} entries ({num_parts} parts)")


if __name__ == "__main__":
    main()
