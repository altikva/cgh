# Graph Schema

```
File --IMPORTS-----------> File
File --DEFINES_FN--------> Function
File --DEFINES_CLASS-----> Class
File --DEFINES_RESOURCE--> TFResource
File --DEFINES_TFVAR-----> TFVar
File --DEFINES_SECTION---> MdSection

Function --CALLS---------> Function
Class --HAS_METHOD-------> Function
Class --INHERITS---------> Class
TFResource --TF_DEPENDS--> TFResource

MdSection --CONTAINS_SECTION--> MdSection    (heading hierarchy)
MdSection --MD_LINKS_TO-------> File         (internal doc links)
MdSection --MD_REFS_SYMBOL----> Function     (code references in docs)
MdSection --MD_REFS_CLASS-----> Class        (code references in docs)
```

---

---

[Back to the README](../README.md)
