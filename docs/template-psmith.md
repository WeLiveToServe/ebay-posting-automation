# Description template (from Psmith in the City, item 177412641967)

Plain HTML, no CSS, three blocks:

1. **Blurb** `<p>` (model-written, 2-4 sentences): what the book is and why it's appealing, then
   one sentence on this copy's edition/binding and its most attractive feature.
2. **Facts** `<ul>` (code from edition data): Title (with subtitle), Author, Publisher (with place
   as on the title page), date as printed ("Title page dated 1919"), Illustrator/plates, Binding,
   Language. Only facts confirmed; no blank rows.
3. **Condition** `<p><b>Condition:</b> ...</p>` (code from condition fields, see
   `grading-rubric.md`).

No price, no shipping talk, no grading tables, no repeated facts between blurb and list beyond
what reads naturally.

## Gold example
```html
<p>P. G. Wodehouse's early Psmith novel, the sequel to "Mike," in which the unflappable Psmith and his friend Mike Jackson are sent to work as clerks at a London bank. This copy, published in London by A. &amp; C. Black with the title page dated 1919, is bound in decorative pictorial cloth. The front cover shows an office scene and the spine shows Psmith at his desk, and both remain bright and colourful.</p>
<ul><li>Title: Psmith in the City: A Sequel to "Mike"</li><li>Author: P. G. Wodehouse</li><li>Publisher: A. &amp; C. Black, Ltd., 4, 5 &amp; 6 Soho Square, London</li><li>Title page dated 1919</li><li>Illustrated by T. M. R. Whitwell, with a frontispiece plate facing the title page</li><li>Binding: hardcover, pictorial cloth</li><li>Language: English</li></ul>
<p><b>Condition:</b> Good. The pictorial cloth is still bright, and the front-cover and spine illustrations are largely intact. The pale cloth shows light general soiling and rubbing, especially near the edges. The head and tail of the spine are rubbed, with some fraying of the cloth, and the corners are lightly bumped and rubbed. The top edge is dust-soiled and the page edges are toned. The title page is toned, with some roughness and wear along its gutter edge. A sound, honest copy of a book now over 100 years old.</p>
```
