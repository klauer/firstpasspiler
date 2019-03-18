# firstpasspiler
First-pass transpiler - parse C++ with clang -> generate templated Python code

The generated code will not work as-is. It likely won't even be valid
Python. That's where the "first pass" comes from - you will have to touch up
the generated code.

Note
----
This is a fun little utility I wrote to help with qtpynodeeditor, in which many
base classes were already wrapped in Python with pyqt5.

It's not likely that I'll continue working on this further.

If you want a more full-featured transpiler, check out
[seasnake](https://github.com/pybee/seasnake).  Also, reach out to 
llvm as this outstanding issue/PR from years ago affects similar projects from
doing what they're supposed to: https://reviews.llvm.org/D10833?id=39176

Limitations
-----------

* Class-centric
* `__init__`, `__del__` will be nonsensical
* `self.` gets prepended at the wrong time
* Not a package
* No tests
* Your output will not work as-is

Requirements
------------

* clang
* inflection
