//! Optional CPU kernels. Sequence matching follows CPython difflib (no junk predicate).
mod process;
use pyo3::prelude::*;
use std::collections::HashMap;

fn ratio(a: &[char], b: &[char]) -> f64 {
    let mut positions: HashMap<char, Vec<usize>> = HashMap::new();
    for (j, &c) in b.iter().enumerate() {
        positions.entry(c).or_default().push(j);
    }
    if b.len() >= 200 {
        let threshold = b.len() / 100 + 1;
        positions.retain(|_, indices| indices.len() <= threshold);
    }
    let mut queue = vec![(0, a.len(), 0, b.len())];
    let mut matches = 0;
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (mut best_i, mut best_j, mut best_size) = (alo, blo, 0);
        let mut previous = vec![0usize; b.len() + 1];
        let mut current = vec![0usize; b.len() + 1];
        for (i, &c) in a.iter().enumerate().take(ahi).skip(alo) {
            current.fill(0);
            if let Some(indices) = positions.get(&c) {
                for &j in indices {
                    if j < blo {
                        continue;
                    }
                    if j >= bhi {
                        break;
                    }
                    let size = previous[j] + 1;
                    current[j + 1] = size;
                    if size > best_size {
                        best_i = i + 1 - size;
                        best_j = j + 1 - size;
                        best_size = size;
                    }
                }
            }
            std::mem::swap(&mut previous, &mut current);
        }
        // Popular characters excluded by autojunk still extend the chosen match.
        while best_i > alo && best_j > blo && a[best_i - 1] == b[best_j - 1] {
            best_i -= 1;
            best_j -= 1;
            best_size += 1;
        }
        while best_i + best_size < ahi
            && best_j + best_size < bhi
            && a[best_i + best_size] == b[best_j + best_size]
        {
            best_size += 1;
        }
        if best_size > 0 {
            matches += best_size;
            if alo < best_i && blo < best_j {
                queue.push((alo, best_i, blo, best_j));
            }
            if best_i + best_size < ahi && best_j + best_size < bhi {
                queue.push((best_i + best_size, ahi, best_j + best_size, bhi));
            }
        }
    }
    if a.len() + b.len() == 0 {
        1.0
    } else {
        2.0 * matches as f64 / (a.len() + b.len()) as f64
    }
}

#[pyfunction]
fn sequence_ratios(anchor: &str, lines: Vec<String>) -> Vec<f64> {
    let a: Vec<char> = anchor.chars().collect();
    lines
        .iter()
        .map(|line| ratio(&a, &line.chars().collect::<Vec<_>>()))
        .collect()
}

#[pyfunction]
fn assignment_candidate(text: &str) -> bool {
    // Python IGNORECASE additionally folds these four non-ASCII letters. Defer all
    // Unicode input to Python rather than risking a missed assignment at this boundary.
    if !text.is_ascii() {
        return true;
    }
    let lower = text.to_ascii_lowercase();
    ["key", "token", "secret", "pass", "pw", "credential", "auth"]
        .iter()
        .any(|word| lower.contains(word))
}

#[pymodule]
fn stardust_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<process::NativeChild>()?;
    m.add_function(wrap_pyfunction!(sequence_ratios, m)?)?;
    m.add_function(wrap_pyfunction!(assignment_candidate, m)?)?;
    Ok(())
}
