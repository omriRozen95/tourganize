# The goal

* I would like you to design the full architecture and demands of a python project.
* I would like to split the full architecture into building blocks (features in the projects), where each one gets an MD file explaining what is the starting state, what should be implemented and what is the definition of done. This building blocks MD-files are sometimes dependant on each other, and therefore there should be some ordering (what other features should be implemented before a specific feature can be implemented). Therefore, I want another MD file that holds the order and dependency of the different building blocks (features).
* It's very important to note that I want to be able to build the project gradually based on the ordering of features (abseloutely not everythingat once).
* It's also important to note that I want the project to be built in domain-driven-design architecture ant that every feature has to be like a "Lego"-piece - easy to match with others given a well-defined input and output but also easy to replace (with a different, perhaps better and upgraded feature).
* pay attention to Lingo in your architecture-plan and set names that are clear and informative but not too restricting.

# Description of the project

The project is to build a user interface that access an LLM behind the hood, where the user request for a trip-design based on given details and demands. a more in-depth review of the project is listed in the following section. The name of the application should be "Tourganize" (tour + organize).

## The key parts of the project

### Interaction with the user

* The user interface should be either GUI or TUI.
* An LLM chatbot that receives questions (or requests) from users.
* The question might be in english or in hebrew. 
* The focus of the question is tourism - The user gives the main details of the tour and ask for a detailed plan back.
* There are different parts in the plan of a tour that should be planned. For example: the flights, the car rentals, the hotels and more. They should be planned in order (start with the most important based on some metric, to be defined later, and then move on to the next most important). In any case, the topics that were specified in the user-request get to be addressed before topics the user didn't mention. For example, the question: "find me a hotel in Paris between the 23 and 28 of October this year" touches hotels explicitly and doesn't touch flights or transportation.
* It's very valid to ask the user if she wants to plan the parts not specified in her question. for example, a question about hotels (as in the point above) can be followed by suggesting her flights-planning or transportation-planning. When suggesting plans for things that were not specificied, it's fine to ask the user if she (or he) would like an help with unspecified topics. based on her response the conversation continues (or ends, if she doesn't want any more help).
* When focusing on a specific topic (for example - flights planning), we should first address any important obscurity. Some matters are blocking (there should be some time range for the plan, if not a specific start date and end date), and some are optional filters (for example the budget of the hotel, the minimal review score of the hotel and so on). Each part of the plan (where parts are hotels, flights, car rentals and so on) has its own set of mandatory and optional filters.
* For each part of the plan (topic, like flights or hotels), the user will receive a choise between several options. Then, the user will choose one of them, or give more details in case she was not pleased with the results. If the user chose one, we can continue to the next part of the plan. If the user gave more details or corrections, we need to plan that part again (using the new details from the user).
* at the end of the conversation a summary of the plan will be written and outputed in a configured format. (default or possible configured value: PDF file)


### Technical demands

* The application should run inside a docker container.
* The application can run in production on a machine that has: 
  - Hardware: 3 usable TU102-based GPUs (1 GeForce RTX 3090 Ti, 2 Quadro RTX 6000). 
  - Software: fully installed and built.
* the LLM must be OSS (open weights model) from "hugging-face". we can, however, rely on Claude-code (I have a subscription) to be the LLM (and the conversation with claude will mimic the conversation with the OSS model of LLM used later), and later build the LLM mechanism needed and plug it in.
* The OSS model LLM is accessed using an API, where we can choose between "Flask" or "FastAPI". It is possible to start with "Flask" and create a task for upgrading to "FastAPI".
* The project must support additional documents to be given. An example is a document containing the regulations of a specific flight company. The information in these additional documents should be accessible to the model either by using RAG (and splitting each document) or by using "Unsloth" to fit the model to the document content.
* In order to access important "world" information, such as available flights, we should use MCP (with FastMCP).
* We should one local MCP service (and you should suggest ideas for that)
